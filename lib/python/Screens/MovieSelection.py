from Screen import Screen
from Components.Button import Button
from Components.ActionMap import HelpableActionMap, ActionMap
from Components.MenuList import MenuList
from Components.MovieList import MovieList, resetMoviePlayState
from Components.DiskInfo import DiskInfo
from Components.Pixmap import Pixmap
from Components.Label import Label
from Components.PluginComponent import plugins
from Components.config import config, ConfigSubsection, ConfigText, ConfigInteger, ConfigLocations, ConfigSet
from Components.Sources.ServiceEvent import ServiceEvent
from Components.Sources.StaticText import StaticText
from Components.UsageConfig import defaultMoviePath
import Components.Harddisk

from Plugins.Plugin import PluginDescriptor

from Screens.MessageBox import MessageBox
from Screens.ChoiceBox import ChoiceBox
from Screens.LocationBox import MovieLocationBox
from Screens.HelpMenu import HelpableScreen

from Tools.Directories import resolveFilename, SCOPE_HDD
from Tools.BoundFunction import boundFunction
import Tools.Trashcan

from enigma import eServiceReference, eServiceCenter, eTimer, eSize
import os
import time
import cPickle as pickle

config.movielist = ConfigSubsection()
config.movielist.moviesort = ConfigInteger(default=MovieList.SORT_RECORDED)
config.movielist.listtype = ConfigInteger(default=MovieList.LISTTYPE_MINIMAL)
config.movielist.description = ConfigInteger(default=MovieList.SHOW_DESCRIPTION)
config.movielist.last_videodir = ConfigText(default=resolveFilename(SCOPE_HDD))
config.movielist.last_timer_videodir = ConfigText(default=resolveFilename(SCOPE_HDD))
config.movielist.videodirs = ConfigLocations(default=[resolveFilename(SCOPE_HDD)])
config.movielist.last_selected_tags = ConfigSet([], default=[])
last_selected_dest = []

preferredTagEditor = None

def setPreferredTagEditor(te):
	global preferredTagEditor
	if preferredTagEditor is None:
		preferredTagEditor = te
		print "Preferred tag editor changed to ", preferredTagEditor
	else:
		print "Preferred tag editor already set to ", preferredTagEditor
		print "ignoring ", te

def getPreferredTagEditor():
	global preferredTagEditor
	return preferredTagEditor

def isTrashFolder(ref):
	if not ref.flags & eServiceReference.mustDescent:
		return False
	return os.path.realpath(ref.getPath()).startswith(Tools.Trashcan.getTrashFolder())

def canDelete(item):
	if not item:
		return False
	if not item[0] or not item[1]:
		return False
	return (item[0].flags & eServiceReference.mustDescent) == 0

def canMove(item):
	if not item:
		return False
	if not item[0] or not item[1]:
		return False
	if item[0].flags & eServiceReference.mustDescent:
		return not isTrashFolder(item[0])
	return True

def canCopy(item):
	if not item:
		return False
	if not item[0] or not item[1]:
		return False
	if item[0].flags & eServiceReference.mustDescent:
		return False
	return True

def createMoveList(serviceref, dest):
	#normpath is to remove the trailing '/' from directories
	src = os.path.normpath(serviceref.getPath())
	srcPath, srcName = os.path.split(src)
	if os.path.normpath(srcPath) == dest:
		# move file to itself is allowed, so we have to check it
		raise Exception, "Refusing to move to the same directory"
	# Make a list of items to move
	moveList = [(src, os.path.join(dest, srcName))]
	if not serviceref.flags & eServiceReference.mustDescent:
		# Real movie, add extra files...
		srcBase = os.path.splitext(src)[0]
		baseName = os.path.split(srcBase)[1]
		eitName =  srcBase + '.eit' 
		if os.path.exists(eitName):
			moveList.append((eitName, os.path.join(dest, baseName+'.eit')))
		baseName = os.path.split(src)[1]
		for ext in ('.ap', '.cuts', '.meta', '.sc'):
			candidate = src + ext
			if os.path.exists(candidate):
				moveList.append((candidate, os.path.join(dest, baseName+ext)))
	return moveList

def moveServiceFiles(serviceref, dest):
	moveList = createMoveList(serviceref, dest)
	# Try to "atomically" move these files
	movedList = []
	try:
		for item in moveList:
			print "[MOVE]", item[0], "->", item[1]
			os.rename(item[0], item[1])
			movedList.append(item)
	except Exception, e:
		print "[MovieSelection] Failed move:", e
		for item in movedList:
			try:
				os.rename(item[1], item[0])
			except:
				print "[MovieSelection] Failed to undo move:", item
		# rethrow exception
		raise

def copyServiceFiles(serviceref, dest):
	# current should be 'ref' type, dest a simple path string
	print "[Movie] Copying to:", dest
	moveList = createMoveList(serviceref, dest)
	# Try to "atomically" move these files
	movedList = []
	try:
		for item in moveList:
			print "[LINK]", item[0], "->", item[1]
			os.link(item[0], item[1])
			movedList.append(item)
		# this worked, we're done
		return
	except Exception, e:
		print "[MovieSelection] Failed copy using link:", e
		for item in movedList:
			try:
				os.unlink(item[1])
			except:
				print "[MovieSelection] Failed to undo copy:", item
	#Link failed, really copy.
	import CopyFiles
	# start with the smaller files, do the big one later.
	moveList.reverse()
	CopyFiles.copyFiles(moveList, os.path.split(moveList[-1][0])[1])
	print "[MovieSelection] Copying in background..."

class MovieContextMenuSummary(Screen):
	def __init__(self, session, parent):
		Screen.__init__(self, session, parent = parent)
		self["selected"] = StaticText("")
		self.onShow.append(self.__onShow)
		self.onHide.append(self.__onHide)

	def __onShow(self):
		self.parent["menu"].onSelectionChanged.append(self.selectionChanged)
		self.selectionChanged()

	def __onHide(self):
		self.parent["menu"].onSelectionChanged.remove(self.selectionChanged)

	def selectionChanged(self):
		item = self.parent["menu"].getCurrent()
		self["selected"].text = item[0]


class MovieContextMenu(Screen):
	# Contract: On OK returns a callable object (e.g. delete)
	def __init__(self, session, csel, service):
		Screen.__init__(self, session)

		self["actions"] = ActionMap(["OkCancelActions"],
			{
				"ok": self.okbuttonClick,
				"cancel": self.cancelClick
			})

		menu = []
		if service:
		 	if (service.flags & eServiceReference.mustDescent):
				if isTrashFolder(service):
					menu.append((_("Permanently remove all deleted items"), csel.purgeAll))
				else:
					menu.append((_("Move"), csel.moveMovie))
			else:
				menu = [(_("Delete"), csel.delete),
					(_("Move"), csel.moveMovie),
					(_("Copy"), csel.copyMovie),
					(_("Reset playback position"), csel.resetMovie),
					]
				# Plugins expect a valid selection, so only include them if we selected a non-dir 
				menu.extend([(p.description, boundFunction(p, session, service)) for p in plugins.getPlugins(PluginDescriptor.WHERE_MOVIELIST)])

		if csel.settings["moviesort"] == MovieList.SORT_ALPHANUMERIC:
			menu.append((_("sort by date"), boundFunction(csel.sortBy, MovieList.SORT_RECORDED)))
		else:
			menu.append((_("alphabetic sort"), boundFunction(csel.sortBy, MovieList.SORT_ALPHANUMERIC)))
		
		menu.extend((
			(_("list style default"), boundFunction(csel.listType, MovieList.LISTTYPE_ORIGINAL)),
			(_("list style compact with description"), boundFunction(csel.listType, MovieList.LISTTYPE_COMPACT_DESCRIPTION)),
			(_("list style compact"), boundFunction(csel.listType, MovieList.LISTTYPE_COMPACT)),
			(_("list style single line"), boundFunction(csel.listType, MovieList.LISTTYPE_MINIMAL))
		))

		if csel.settings["description"] == MovieList.SHOW_DESCRIPTION:
			menu.append((_("hide extended description"), boundFunction(csel.showDescription, MovieList.HIDE_DESCRIPTION)))
		else:
			menu.append((_("show extended description"), boundFunction(csel.showDescription, MovieList.SHOW_DESCRIPTION)))
		self["menu"] = MenuList(menu)

	def createSummary(self):
		return MovieContextMenuSummary

	def okbuttonClick(self):
		self.close(self["menu"].getCurrent()[1])

	def cancelClick(self):
		self.close(None)

class SelectionEventInfo:
	def __init__(self):
		self["Service"] = ServiceEvent()
		self.list.connectSelChanged(self.__selectionChanged)
		self.timer = eTimer()
		self.timer.callback.append(self.updateEventInfo)
		self.onShown.append(self.__selectionChanged)

	def __selectionChanged(self):
		if self.execing and self.settings["description"] == MovieList.SHOW_DESCRIPTION:
			self.timer.start(100, True)

	def updateEventInfo(self):
		serviceref = self.getCurrent()
		self["Service"].newService(serviceref)

class MovieSelectionSummary(Screen):
	# Kludgy component to display current selection on LCD. Should use
	# parent.Service as source for everything, but that seems to have a
	# performance impact as the MovieSelection goes through hoops to prevent
	# this when the info is not selected
	def __init__(self, session, parent):
		Screen.__init__(self, session, parent = parent)
		self["name"] = StaticText("")
		self.onShow.append(self.__onShow)
		self.onHide.append(self.__onHide)

	def __onShow(self):
		self.parent.list.connectSelChanged(self.selectionChanged)
		self.selectionChanged()

	def __onHide(self):
		self.parent.list.disconnectSelChanged(self.selectionChanged)

	def selectionChanged(self):
		item = self.parent.getCurrentSelection()
		if item and item[0]:
			if not item[1]:
				# special case, one up
				name = ".."
			else:
				name = item[1].getName(item[0])
				if (item[0].flags & eServiceReference.mustDescent):
					if len(name) > 12:
						name = os.path.split(os.path.normpath(name))[1]
					name = "> " + name
			self["name"].text = name
		else:
			self["name"].text = ""


class MovieSelection(Screen, HelpableScreen, SelectionEventInfo):
	def __init__(self, session, selectedmovie = None):
		Screen.__init__(self, session)
		HelpableScreen.__init__(self)

		self.tags = {}
		if selectedmovie:
			self.selected_tags = config.movielist.last_selected_tags.value
		else:
			self.selected_tags = None
		self.selected_tags_ele = None

		self.movemode = False
		self.bouquet_mark_edit = False

		self.delayTimer = eTimer()
		self.delayTimer.callback.append(self.updateHDDData)

		self["waitingtext"] = Label(_("Please wait... Loading list..."))

		# create optional description border and hide immediately
		self["DescriptionBorder"] = Pixmap()
		self["DescriptionBorder"].hide()

		if not os.path.isdir(config.movielist.last_videodir.value):
			config.movielist.last_videodir.value = defaultMoviePath()
			config.movielist.last_videodir.save()
		self.current_ref = eServiceReference("2:0:1:0:0:0:0:0:0:0:" + config.movielist.last_videodir.value)

		self.settings = {\
			"listtype": config.movielist.listtype.value,
			"moviesort": config.movielist.moviesort.value,
			"description": config.movielist.description.value
		}
		self["list"] = MovieList(None, list_type=self.settings["listtype"], sort_type=self.settings["moviesort"], descr_state=self.settings["description"])

		self.list = self["list"]
		self.selectedmovie = selectedmovie

		# Need list for init
		SelectionEventInfo.__init__(self)

		self["key_red"] = Button(_("Delete"))
		self["key_green"] = Button(_("Move"))
		self["key_yellow"] = Button(_("Location"))
		self["key_blue"] = Button(_("Tags"))

		self["freeDiskSpace"] = self.diskinfo = DiskInfo(config.movielist.last_videodir.value, DiskInfo.FREE, update=False)

		self["InfobarActions"] = HelpableActionMap(self, "InfobarActions", 
			{
				"showMovies": (self.doPathSelect, _("select the movie path")),
			})

		self["NumberActions"] =  HelpableActionMap(self, "NumberActions", 
			{
				"2": (self.list.moveToFirst, _("Go to top of list")),
				"5": (self.list.moveToFirstMovie, _("Go to first movie")),
				"8": (self.list.moveToLast, _("Go to last item")),
			})

		self["MovieSelectionActions"] = HelpableActionMap(self, "MovieSelectionActions",
			{
				"contextMenu": (self.doContext, _("menu")),
				"showEventInfo": (self.showEventInformation, _("show event details")),
			})

		self["ColorActions"] = HelpableActionMap(self, "ColorActions",
			{
				"red": (self.delete, _("delete...")),
				"green": (self.moveMovie, _("Move to other directory")),
				"yellow": (self.showBookmarks, _("select the movie path")),
				"blue": (self.showTagsSelect, _("show tag menu")),
			})

		self["OkCancelActions"] = HelpableActionMap(self, "OkCancelActions",
			{
				"cancel": (self.abort, _("exit movielist")),
				"ok": (self.itemSelected, _("select movie")),
			})

		self.onShown.append(self.go)
		self.onLayoutFinish.append(self.saveListsize)
		self.list.connectSelChanged(self.updateButtons)
		self.inited = False

	def createSummary(self):
		return MovieSelectionSummary
		
	def updateDescription(self):
		if self.settings["description"] == MovieList.SHOW_DESCRIPTION:
			self["DescriptionBorder"].show()
			self["list"].instance.resize(eSize(self.listWidth, self.listHeight-self["DescriptionBorder"].instance.size().height()))
		else:
			self["Service"].newService(None)
			self["DescriptionBorder"].hide()
			self["list"].instance.resize(eSize(self.listWidth, self.listHeight))
			
	def updateButtons(self):
		item = self.getCurrentSelection()
		if canDelete(item):
			self["key_red"].show()
		else:
			self["key_red"].hide()
		if canMove(item):
			self["key_green"].show()
		else:
			self["key_green"].hide()

	def showEventInformation(self):
		from Screens.EventView import EventViewSimple
		from ServiceReference import ServiceReference
		evt = self["list"].getCurrentEvent()
		if evt:
			self.session.open(EventViewSimple, evt, ServiceReference(self.getCurrent()))

	def go(self):
		if not self.inited:
		# ouch. this should redraw our "Please wait..."-text.
		# this is of course not the right way to do this.
			self.delayTimer.start(10, 1)
			self.inited=True

	def saveListsize(self):
			listsize = self["list"].instance.size()
			self.listWidth = listsize.width()
			self.listHeight = listsize.height()
			self.updateDescription()

	def updateHDDData(self):
 		self.reloadList(self.selectedmovie, home=True)
		self["waitingtext"].visible = False

	def moveTo(self):
		self["list"].moveTo(self.selectedmovie)

	def getCurrent(self):
		# Returns selected serviceref (may be None)
		return self["list"].getCurrent()
		
	def getCurrentSelection(self):
		# Returns None or (serviceref, info, begin, len)
		return self["list"].l.getCurrentSelection()

	def itemSelected(self):
		current = self.getCurrent()
		if current is not None:
			if current.flags & eServiceReference.mustDescent:
				self.gotFilename(current.getPath())
			else:
				self.movieSelected()

	# Note: DVDBurn overrides this method, hence the itemSelected indirection.
	def movieSelected(self):
		current = self.getCurrent()
		if current is not None:
			self.saveconfig()
			self.close(current)

	def doContext(self):
		current = self.getCurrent()
		if current is not None:
			self.session.openWithCallback(self.doneContext, MovieContextMenu, self, current)

	def doneContext(self, action):
		if action is not None:
			action()

	def saveLocalSettings(self):
		try:
			path = os.path.join(config.movielist.last_videodir.value, "e2settings.pkl")
			pickle.dump(self.settings, open(path, "wb"))
		except Exception, e:
			print "Failed to save settings:", e
		# Also set config items, in case the user has a read-only disk 
		config.movielist.moviesort.value = self.settings["moviesort"]
		config.movielist.listtype.value = self.settings["listtype"]
		config.movielist.description.value = self.settings["description"]

	def loadLocalSettings(self):
		'Load settings, called when entering a directory'
		try:
			try:
				path = os.path.join(config.movielist.last_videodir.value, ".e2settings.pkl")
				updates = pickle.load(open(path, "rb"))
			except:
				# load old settings file and rename it (this code should be removed in a week or so)
				oldpath = os.path.join(config.movielist.last_videodir.value, "e2settings.pkl")
				updates = pickle.load(open(oldpath, "rb"))
				try:
					os.rename(oldpath, path)
				except:
					pass
			needUpdateDesc = ("description" in updates) and (updates["description"] != self.settings["description"]) 
			self.settings.update(updates)
			if needUpdateDesc:
				self["list"].setDescriptionState(self.settings["description"])
				self.updateDescription()
			self["list"].setListType(self.settings["listtype"])
			self["list"].setSortType(self.settings["moviesort"])
		except Exception, e:
			print "Failed to load settings:", e


	def sortBy(self, newType):
		self.settings["moviesort"] = newType
		self.saveLocalSettings()
		self.setSortType(newType)
		self.reloadList()

	def listType(self, newType):
		self.settings["listtype"] = newType
		self.saveLocalSettings()
		self.setListType(newType)

	def showDescription(self, newType):
		self.settings["description"] = newType
		self.saveLocalSettings()
		self.setDescriptionState(newType)
		self.updateDescription()

	def abort(self):
		self.saveconfig()
		self.close(None)

	def saveconfig(self):
		config.movielist.last_selected_tags.value = self.selected_tags

	def getTagDescription(self, tag):
		# TODO: access the tag database
		return tag

	def updateTags(self):
		# get a list of tags available in this list
		self.tags = self["list"].tags

		# the rest is presented in a list, available on the
		# fourth ("blue") button
		if self.tags:
			self["key_blue"].text = _("Tags")+"..."
		else:
			self["key_blue"].text = ""

	def setListType(self, type):
		self["list"].setListType(type)

	def setDescriptionState(self, val):
		self["list"].setDescriptionState(val)

	def setSortType(self, type):
		self["list"].setSortType(type)

	def reloadList(self, sel = None, home = False):
		if not os.path.isdir(config.movielist.last_videodir.value):
			path = defaultMoviePath()
			config.movielist.last_videodir.value = path
			config.movielist.last_videodir.save()
			self.current_ref = eServiceReference("2:0:1:0:0:0:0:0:0:0:" + path)
			self["freeDiskSpace"].path = path
		if sel is None:
			sel = self.getCurrent()
		self.loadLocalSettings()
		self["list"].reload(self.current_ref, self.selected_tags)
		title = _("Recorded files...")
		if config.usage.setup_level.index >= 2: # expert+
			title += "  " + config.movielist.last_videodir.value
		if self.selected_tags is not None:
			title += " - " + ','.join(self.selected_tags)
		self.setTitle(title)
 		if not (sel and self["list"].moveTo(sel)):
			if home:
				self["list"].moveToFirstMovie()
		self.updateTags()
		self["freeDiskSpace"].update()

	def doPathSelect(self):
		self.session.openWithCallback(
			self.gotFilename,
			MovieLocationBox,
			_("Please select the movie path..."),
			config.movielist.last_videodir.value
		)

	def gotFilename(self, res):
		if not res:
			return
		# serviceref must end with /
		if not res.endswith('/'):
			res += '/'
		if res is not config.movielist.last_videodir.value:
			if os.path.isdir(res):
				config.movielist.last_videodir.value = res
				config.movielist.last_videodir.save()
				self.current_ref = eServiceReference("2:0:1:0:0:0:0:0:0:0:" + res)
				self["freeDiskSpace"].path = res
				self.reloadList(home = True)
			else:
				self.session.open(
					MessageBox,
					_("Directory %s nonexistent.") % (res),
					type = MessageBox.TYPE_ERROR,
					timeout = 5
					)

	def showAll(self):
		self.selected_tags_ele = None
		self.selected_tags = None
		self.reloadList(home = True)

	def showTagsN(self, tagele):
		if not self.tags:
			self.showTagWarning()
		elif not tagele or (self.selected_tags and tagele.value in self.selected_tags) or not tagele.value in self.tags:
			self.showTagsMenu(tagele)
		else:
			self.selected_tags_ele = tagele
			self.selected_tags = self.tags[tagele.value]
			self.reloadList(home = True)

	def showTagsFirst(self):
		self.showTagsN(config.movielist.first_tags)

	def showTagsSecond(self):
		self.showTagsN(config.movielist.second_tags)

	def showTagsSelect(self):
		self.showTagsN(None)

	def tagChosen(self, tag):
		if tag is not None:
			if tag[1] is None: # all
				self.showAll()
				return
			# TODO: Some error checking maybe, don't wanna crash on KeyError
			self.selected_tags = self.tags[tag[0]]
			if self.selected_tags_ele:
				self.selected_tags_ele.value = tag[0]
				self.selected_tags_ele.save()
			self.reloadList(home = True)

	def showTagsMenu(self, tagele):
		self.selected_tags_ele = tagele
		lst = [(_("show all tags"), None)] + [(tag, self.getTagDescription(tag)) for tag in self.tags]
		self.session.openWithCallback(self.tagChosen, ChoiceBox, title=_("Please select tag to filter..."), list = lst)

	def showTagWarning(self):
		self.session.open(MessageBox, _("No tags are set on these movies."), MessageBox.TYPE_ERROR)

	def selectMovieLocation(self, title, callback):
		bookmarks = [("("+_("Other")+"...)", None)]
		inlist = []
		for d in config.movielist.videodirs.value:
			d = os.path.normpath(d)
			bookmarks.append((d,d))
			inlist.append(d)
		for p in Components.Harddisk.harddiskmanager.getMountedPartitions():
			d = os.path.normpath(p.mountpoint)
			bookmarks.append((p.description, d))
			inlist.append(d)
		for d in last_selected_dest:
			if d not in inlist:
				bookmarks.append((d,d))
		self.onMovieSelected = callback
		self.movieSelectTitle = title
		self.session.openWithCallback(self.gotMovieLocation, ChoiceBox, title=title, list = bookmarks)

	def gotMovieLocation(self, choice):
		if not choice:
			# cancelled
			self.onMovieSelected(None)
			del self.onMovieSelected
			return
		if isinstance(choice, tuple):
			if choice[1] is None:
				# Display full browser, which returns string
				self.session.openWithCallback(
					self.gotMovieLocation,
					MovieLocationBox,
					self.movieSelectTitle,
					config.movielist.last_videodir.value
				)
				return
			choice = choice[1]
		choice = os.path.normpath(choice)
		self.rememberMovieLocation(choice)
		self.onMovieSelected(choice)
		del self.onMovieSelected

	def rememberMovieLocation(self, where):
		print "[--Movie--] rememberMovieLocation", where
		if where in last_selected_dest:
			last_selected_dest.remove(where)
		last_selected_dest.insert(0, where)
		if len(last_selected_dest) > 5:
			del last_selected_dest[-1]
		print last_selected_dest

	def showBookmarks(self):
		self.selectMovieLocation(title=_("Please select the movie path..."), callback=self.gotFilename)

	def resetMovie(self):
		current = self.getCurrent()
		if current:
			resetMoviePlayState(current.getPath() + ".cuts")
			self["list"].invalidateCurrentItem() # trigger repaint

	def moveMovie(self):
		item = self.getCurrentSelection() 
		if canMove(item):
			current = item[0]
			info = item[1]
			if info is None:
				# Special case
				return
			name = info and info.getName(current) or _("this recording")
			path = os.path.normpath(current.getPath())
			# show a more limited list of destinations, no point
			# in showing mountpoints.
			title = _("Select destination for:") + " " + name
			bookmarks = [("("+_("Other")+"...)", None)]
			inlist = []
			# Subdirs
			try:
				base = os.path.split(path)[0]
				for fn in os.listdir(base):
					if not fn.startswith('.'): # Skip hidden things
						d = os.path.join(base, fn)
						if os.path.isdir(d) and (d not in inlist):
							bookmarks.append((fn,d))
							inlist.append(d)
			except Exception, e :
				print "[MovieSelection]", e
			# Last favourites
			for d in last_selected_dest:
				if d not in inlist:
					bookmarks.append((d,d))
			# Other favourites
			for d in config.movielist.videodirs.value:
				d = os.path.normpath(d)
				bookmarks.append((d,d))
				inlist.append(d)
			self.onMovieSelected = self.gotMoveMovieDest
			self.movieSelectTitle = title
			self.session.openWithCallback(self.gotMovieLocation, ChoiceBox, title=title, list=bookmarks)

	def gotMoveMovieDest(self, choice):
		if not choice:
			return
		dest = os.path.normpath(choice)
		try:
			current = self.getCurrent()
			moveServiceFiles(current, dest)
			self["list"].removeService(current)
		except Exception, e:
			self.session.open(MessageBox, str(e), MessageBox.TYPE_ERROR)

	def copyMovie(self):
		item = self.getCurrentSelection() 
		if canMove(item):
			current = item[0]
			info = item[1]
			if info is None:
				# Special case
				return
			name = info and info.getName(current) or _("this recording")
			self.selectMovieLocation(title=_("Select copy destination for:") + " " + name, callback=self.gotCopyMovieDest)

	def gotCopyMovieDest(self, choice):
		if not choice:
			return
		dest = os.path.normpath(choice)
		try:
			current = self.getCurrent()
			copyServiceFiles(current, dest)
		except Exception, e:
			self.session.open(MessageBox, str(e), MessageBox.TYPE_ERROR)

	def delete(self):
		item = self.getCurrentSelection()
		if not canDelete(item):
			if item and isTrashFolder(item[0]):
				# Red button to empty trashcan...
				self.purgeAll()
			return
		current = item[0]
		info = item[1]
		if current and (not current.flags & eServiceReference.mustDescent):
			if config.usage.movielist_trashcan.value:
				try:
					trash = Tools.Trashcan.createTrashFolder()
					# Also check whether we're INSIDE the trash, then it's a purge.
					if os.path.realpath(current.getPath()).startswith(trash):
						msg = _("Deleted items") + "\n"
					else:
						moveServiceFiles(self.getCurrent(), trash)
						self["list"].removeService(current)
						# Files were moved to .Trash, ok.
						return
				except OSError, e:
					print "[MovieSelection] Cannot move to trash", e
					if e.errno == 18:
						# This occurs when moving across devices
						msg = _("Cannot move files on a different disk or system to the trash can") + ". "
					else:
						msg = _("Cannot move to trash can") + ".\n" + e.message + "\n"
				except Exception, e:
					print "[MovieSelection] Weird error moving to trash", e
					# Failed to create trash or move files.
					msg = _("Cannot move to trash can") + "\n" + str(e) + "\n"
			else:
				msg = ''
			name = info and info.getName(current) or _("this recording")
			self.session.openWithCallback(self.deleteConfirmed, MessageBox, msg + _("Do you really want to delete %s?") % (name))

	def deleteConfirmed(self, confirmed):
		if not confirmed:
			return
		current = self.getCurrent()
		if current is None:
			# huh?
			return
		serviceHandler = eServiceCenter.getInstance()
		offline = serviceHandler.offlineOperations(current)
		result = False
		if offline is not None:
			# really delete!
			if not offline.deleteFromDisk(0):
				result = True
		if result == False:
			self.session.open(MessageBox, _("Delete failed!"), MessageBox.TYPE_ERROR)
		else:
			self["list"].removeService(current)
			# Todo: This is pointless, delete is not finished yet.
			self["freeDiskSpace"].update()

	def purgeAll(self):
		recordings = self.session.nav.getRecordings()
		next_rec_time = -1
		if not recordings:
			next_rec_time = self.session.nav.RecordTimer.getNextRecordingTime()	
		if recordings or (next_rec_time > 0 and (next_rec_time - time.time()) < 120):
			msg = "\n" + _("Recording(s) are in progress or coming up in few seconds!")
		else:
			msg = ""
		self.session.openWithCallback(self.purgeConfirmed, MessageBox, _("Permanently delete all recordings in the trash can?") + msg)
	
	def purgeConfirmed(self, confirmed):
		if not confirmed:
			return
		Tools.Trashcan.cleanAll()
