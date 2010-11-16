#include <lib/gdi/sdl.h>

#include <lib/base/init.h>
#include <lib/base/init_num.h>

#include <SDL.h>

gSDLDC::gSDLDC()
{
	if (SDL_Init(SDL_INIT_VIDEO) < 0)
	{
		eWarning("Could not initialize SDL: %s", SDL_GetError());
		return;
	}

	setResolution(720, 576);

	CONNECT(m_pump.recv_msg, gSDLDC::pumpEvent);

	m_surface.type = 0;
	m_surface.clut.colors=256;
	m_surface.clut.data=new gRGB[m_surface.clut.colors];
	
	m_pixmap = new gPixmap(&m_surface);
	
	memset(m_surface.clut.data, 0, sizeof(*m_surface.clut.data)*m_surface.clut.colors);
}

gSDLDC::~gSDLDC()
{
	SDL_Quit();
}

void gSDLDC::setPalette()
{
	if (!m_surface.clut.data)
		return;
	
/*	for (int i=0; i<256; ++i)
	{
		fb->CMAP()->red[i]=ramp[m_surface.clut.data[i].r]<<8;
		fb->CMAP()->green[i]=ramp[m_surface.clut.data[i].g]<<8;
		fb->CMAP()->blue[i]=ramp[m_surface.clut.data[i].b]<<8;
		fb->CMAP()->transp[i]=rampalpha[m_surface.clut.data[i].a]<<8;
		if (!fb->CMAP()->red[i])
			fb->CMAP()->red[i]=0x100;
	}
	fb->PutCMAP(); */
}

void gSDLDC::exec(const gOpcode *o)
{
	switch (o->opcode)
	{
	case gOpcode::setPalette:
	{
		gDC::exec(o);
		setPalette();
		break;
	}
	case gOpcode::flush:
		SDL_Flip(m_screen);
		eDebug("FLUSH");
		break;
	default:
		gDC::exec(o);
		break;
	}
}

void gSDLDC::setResolution(int xres, int yres)
{
	m_screen = SDL_SetVideoMode(xres, yres, 32, SDL_HWSURFACE);
	if (!m_screen)
	{
		eWarning("Could not create SDL surface: %s", SDL_GetError());
		return;
	}

	m_surface.x = m_screen->w;
	m_surface.y = m_screen->h;
	m_surface.bpp = m_screen->format->BitsPerPixel;
	m_surface.bypp = m_screen->format->BytesPerPixel;
	m_surface.stride = m_screen->pitch;
	m_surface.data = m_screen->pixels;
}

eAutoInitPtr<gSDLDC> init_gSDLDC(eAutoInitNumbers::graphic-1, "gSDLDC");
