#ifndef __lib_base_filepush_h
#define __lib_base_filepush_h

#include <lib/base/thread.h>
#include <lib/base/ioprio.h>
#include <libsig_comp.h>
#include <lib/base/message.h>
#include <sys/types.h>
#include <lib/base/rawfile.h>

class iFilePushScatterGather
{
public:
	virtual void getNextSourceSpan(off_t current_offset, size_t bytes_read, off_t &start, size_t &size)=0;
	virtual ~iFilePushScatterGather() {}
};

class eFilePushThread: public eThread, public Object
{
	int prio_class, prio;
	std::string streamUrl;
	int streamFd;
	int connectStream(std::string &url);
	ssize_t socketRead(int fd, void *buf, size_t count);
	ssize_t timedSocketRead(int fd, void *data, size_t size, int msinitial, int msinterbyte = 200);
	ssize_t socketWrite(int fd, const void *buf, size_t count);

public:
	eFilePushThread(int prio_class=IOPRIO_CLASS_BE, int prio_level=0, int blocksize=188);
	~eFilePushThread();
	void thread();
	void stop();
	void start(int sourcefd, int destfd);
	int start(const char *filename, int destfd);
	int startUrl(const char *url, int destfd);
	
	void pause();
	void seek(int whence, off_t where);
	void resume();
	
		/* flushes the internal readbuffer */ 
	void flush();
	void enablePVRCommit(int);
	
		/* stream mode will wait on EOF until more data is available. */
	void setStreamMode(int);
	
	void setScatterGather(iFilePushScatterGather *);
	
	enum { evtEOF, evtReadError, evtWriteError, evtUser };
	Signal1<void,int> m_event;

	void installSigUSR1Handler();
	void before_set_thread_alive();

		/* you can send private events if you want */
	void sendEvent(int evt);
protected:
	virtual int filterRecordData(const unsigned char *data, int len, size_t &current_span_remaining);
private:
	iFilePushScatterGather *m_sg;
	int m_stop;
	int m_buf_start, m_buf_end, m_filter_end;
	int m_fd_dest;
	int m_send_pvr_commit;
	int m_stream_mode;
	int m_blocksize;

	eRawFile m_raw_source;
	
	eFixedMessagePump<int> m_messagepump;

	unsigned char m_buffer[188*1024]; // Align to page (4096) and DMX (188)
	
	void recvEvent(const int &evt);
};

#endif
