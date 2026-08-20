// IkaEasy runs behind whatever host actually serves the game — the official
// ikariam.gameforge.com domain, the ikabot web server on 127.0.0.1, a LAN IP,
// or a remote-access host such as a Tailscale 100.x address or a MagicDNS
// "*.ts.net" name. We can no longer identify the game by its URL, so the
// content script is injected everywhere and decides for itself whether the
// current page is Ikariam by looking at the page's own markup — which is the
// same no matter which host delivered it.

function isIkariamPage() {
    // 1. The official Ikariam domain is always the game.
    try {
        if (/(^|\.)ikariam\.gameforge\.com$/i.test(location.hostname)) {
            return true;
        }
    } catch (e) {}

    // 2. Any other host (proxy / VPN / LAN / custom hostname): identify the
    //    game by structural DOM markers that Ikariam always renders. These are
    //    stable element IDs the game uses across the city, island, world and
    //    options views.
    try {
        if (document.getElementById('container') && (
                document.getElementById('GF_toolbar')     ||
                document.getElementById('cityMenu')       ||
                document.getElementById('worldmap')       ||
                document.getElementById('js_islandBackground') ||
                document.getElementById('changeCityBox')  ||
                document.querySelector('div.templateView, body.ikariam, #js_ChangeCityBox'))) {
            return true;
        }

        // 3. Fallback: Ikariam's inline bootstrap scripts survive proxy URL
        //    rewriting (only asset src/href get rewritten, not script bodies),
        //    so their signature is a reliable last-resort marker.
        const html = document.documentElement.innerHTML;
        if (/ikariam\.(model|backgroundView|getClass|features)\b/.test(html) ||
            /\bnew\s+Ikariam\b/.test(html) ||
            /LocalizationStrings\s*[:=]/.test(html)) {
            return true;
        }
    } catch (e) {}

    return false;
}

// Only boot the extension on real Ikariam pages. On every other site this
// content script does nothing beyond this cheap check.
if (isIkariamPage()) {
    // Подключаем initModule чтобы потом нормально работать с импортами
    import('./initModule.js');
}
