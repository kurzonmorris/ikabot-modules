==========================================================
  ikabot in Docker  —  installer v1.0.18
==========================================================

Runs ikabot for as many game accounts as you like, in one
container, with a web control panel. Everything stays on
YOUR machine — your logins never leave it.


WHAT YOU NEED
-------------
  * A PC, or a server such as Unraid or TrueNAS
  * Docker
      Windows / Mac : install Docker Desktop from
                      https://www.docker.com/products/docker-desktop/
      Unraid        : Settings -> Docker -> Enable = Yes
      Linux         : https://docs.docker.com/engine/install/


INSTALLING — WINDOWS OR MAC
---------------------------
  1. Start Docker Desktop and wait for "Engine running".
  2. Double-click  INSTALL.bat
  3. Answer three questions: where to keep data, how many
     accounts, and a password for the web pages.

  It builds and starts everything, then opens the control
  panel in your browser.


INSTALLING — UNRAID, TRUENAS OR LINUX
-------------------------------------
  Open a terminal in this folder and run:

      ./install.sh

  Same three questions, same result.

  Updating later? Extract the new zip into a FRESH
  folder rather than over the old one. Unzipping on
  top leaves the previous panel file behind beside
  the new one. The build copes -- it takes the newer
  and says so -- but a clean folder is tidier.


AFTER INSTALLING
----------------
  Control panel : http://localhost:7682     (or your server's IP)
  Terminal      : http://localhost:7681

  Use the terminal to log each account in the first time.
  Press Ctrl-B then W to switch between accounts,
  Ctrl-B then D to leave the screens running.

  The control panel handles restarts, updates and modules.


WHERE YOUR DATA LIVES
---------------------
  Two folders are made where you chose:

    config/  your saved logins, sessions and settings.
             Keep this backed up. Do not share it.
    app/     ikabot itself. Replaced by "ika update";
             re-running this installer leaves it alone.


UPDATING LATER
--------------
  From the control panel, or from a terminal:

      docker exec -it ikabot ika update        (ikabot itself)
      docker exec -it ikabot ika modules       (the modules)
      docker exec -it ikabot ika panel upgrade (the panel)

  You do not need to download this installer again.


A NOTE ON WINDOWS
-----------------
  On Windows and Mac, Docker cannot share the host's
  network, so the per-account web servers (ikabot menu
  option 16) are not reachable from your browser. The
  control panel and terminal work normally. On Unraid,
  TrueNAS and Linux everything works.
