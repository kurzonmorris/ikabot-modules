import { Time } from './const.js';

export function getThisKey(add = null) {
  let key = [getServerDomain(), getServerWorld(), Front.data.avatarId];
  if (add) {
    key.push(add);
  }

  return key.join('-');
}

export function setItem(name, value, time) {
  let v = {};
  v["value"]  = value;
  v["expire"] = (time) ? _.now() + time * 1000: 0;

  localStorage.setItem(getThisKey(name), JSON.stringify(v));
  return true;
}

export function updateObjectItem(name, values, time, def = {}) {
  let curValue = getItem(name) || def;
  let v = {
    value: _.defaults(curValue, values),
    expire: (time) ? _.now() + time * 1000: 0
  };

  localStorage.setItem(getThisKey(name), JSON.stringify(v));
  return true;
}

export function getItem(name){
  let t = localStorage.getItem(getThisKey(name));
  let v = (!t) ? false : ((t !== 'false') ? JSON.parse(t) : false);
  if (v){
    if ((v['expire'] > 0) && (v['expire'] < _.now())) {
      removeItem(name);
      v = false;
    }
  }

  return (v) ? v['value'] : null;
}

export function removeItem(name) {
  return localStorage.removeItem(getThisKey(name));
}


export function getServerId() {
  let server = getServerDomain();
  const servers = [{"server_id":"1","server_short":"ru","server_name":"Russia"},{"server_id":"2","server_short":"en","server_name":"United Kingdom"},{"server_id":"3","server_short":"de","server_name":"Germany"},{"server_id":"4","server_short":"ar","server_name":"Argentina"},{"server_id":"5","server_short":"ba","server_name":"Balkan"},{"server_id":"6","server_short":"by","server_name":"Belarus"},{"server_id":"7","server_short":"br","server_name":"Brazil"},{"server_id":"8","server_short":"bg","server_name":"Bulgaria"},{"server_id":"9","server_short":"cl","server_name":"Chile"},{"server_id":"10","server_short":"co","server_name":"Colombia"},{"server_id":"11","server_short":"cz","server_name":"Czech Repuplic"},{"server_id":"12","server_short":"dk","server_name":"Denmark"},{"server_id":"13","server_short":"ee","server_name":"Estonia"},{"server_id":"14","server_short":"fi","server_name":"Finland"},{"server_id":"15","server_short":"fr","server_name":"France"},{"server_id":"16","server_short":"gr","server_name":"Greece"},{"server_id":"17","server_short":"hk","server_name":"Hong Kong"},{"server_id":"18","server_short":"hu","server_name":"Hungary"},{"server_id":"19","server_short":"id","server_name":"Indonesia"},{"server_id":"20","server_short":"ir","server_name":"Iran"},{"server_id":"21","server_short":"il","server_name":"Israel"},{"server_id":"22","server_short":"it","server_name":"Italy"},{"server_id":"23","server_short":"lv","server_name":"Latvia"},{"server_id":"24","server_short":"lt","server_name":"Lithuania"},{"server_id":"25","server_short":"mx","server_name":"Mexico"},{"server_id":"26","server_short":"nl","server_name":"Netherlands"},{"server_id":"27","server_short":"no","server_name":"Norway"},{"server_id":"28","server_short":"pk","server_name":"Pakistan"},{"server_id":"29","server_short":"pe","server_name":"Peru"},{"server_id":"30","server_short":"ph","server_name":"Philippines"},{"server_id":"31","server_short":"pl","server_name":"Poland"},{"server_id":"32","server_short":"pt","server_name":"Portugal"},{"server_id":"33","server_short":"ro","server_name":"Romania"},{"server_id":"34","server_short":"rs","server_name":"Serbia"},{"server_id":"35","server_short":"sk","server_name":"Slovakia"},{"server_id":"36","server_short":"si","server_name":"Slovenia"},{"server_id":"37","server_short":"es","server_name":"Spain"},{"server_id":"38","server_short":"se","server_name":"Sweden"},{"server_id":"39","server_short":"tw","server_name":"Taiwan"},{"server_id":"40","server_short":"tr","server_name":"Turkey"},{"server_id":"41","server_short":"ae","server_name":"United Arab Emirates"},{"server_id":"42","server_short":"us","server_name":"United States of America"},{"server_id":"43","server_short":"ve","server_name":"Venezuela"},{"server_id":"44","server_short":"vn","server_name":"Vietnam"}];

  let s = _.find(servers, {server_short: server});
  return s.server_id || 1;
}

export function getServerDomain() {
  // Try to get from URL first (official domain)
  let hostMatch = /(s\d+)-([a-z]+)?\.ikariam.gameforge.com/i.exec(top.location.host);
  if (hostMatch && hostMatch[2]) {
    return hostMatch[2];
  }
  // Try to get from game data (works with proxy/IP access)
  if (typeof Front !== 'undefined' && Front.data && Front.data.serverName) {
    // serverName format is typically like "Alpha" or similar, but we need the domain code
    // Try to extract from other Front.data properties
    if (Front.data.language) {
      return Front.data.language;
    }
  }
  // Try to get from page content or cookies
  try {
    let serverMatch = document.cookie.match(/ikariam_server[^=]*=([^;]+)/i);
    if (serverMatch) {
      return serverMatch[1];
    }
  } catch(e) {}
  return 'en';
}

export function getServerWorld() {
  // Try to get from URL first (official domain)
  let hostMatch = /(s\d+)-([a-z]+)?\.ikariam.gameforge.com/i.exec(top.location.host);
  if (hostMatch && hostMatch[1]) {
    return hostMatch[1];
  }
  // Try to get from game data (works with proxy/IP access)
  if (typeof Front !== 'undefined' && Front.data) {
    // Try serverName or world identifier from game data
    if (Front.data.worldId) {
      return 's' + Front.data.worldId;
    }
  }
  // Try to extract from URL path (game sometimes includes server in path)
  try {
    let pathMatch = /[?&]world=(\d+)/i.exec(top.location.search);
    if (pathMatch) {
      return 's' + pathMatch[1];
    }
  } catch(e) {}
  return 's1';
}

export function getInt(a) {
  if (!a) {
    return 0;
  }

  return parseInt(a.replace(/[^\d-]+/g, '')) || 0;
}

export function getFloat(a, separators) {
  if (!a) {
    return 0;
  }

  let sep = (typeof Front !== 'undefined') ? { thousand: Front.data.localizationStrings.thousandSeperator, decimal: Front.data.localizationStrings.decimalPoint } : separators;
  a = a.trim().replace(new RegExp(_.escapeRegExp(sep.thousand), 'g'), '').replace(new RegExp(_.escapeRegExp(sep.decimal), 'g'), '.');
  return parseFloat(a.replace(/[^\d-.]+/g, ''));
}

export function handleSize(size) {
  if ((typeof size === 'number') || (/^([0-9]+)$/.test(size))) {
    return `${size}px`;
  }

  return size;
}

export function formatNumber(number, def, forceShowSign = true, separators) {
  def = def || '0';
  let sep = (typeof Front !== 'undefined') ? { thousand: Front.data.localizationStrings.thousandSeperator, decimal: Front.data.localizationStrings.decimalPoint } : separators;
  let result = ((number || "") + "").replace(/(\d)(?=(?:\d{3})+(?:$|\.|,))/g, "$1" + sep.thousand).replace(".", sep.decimal);

  if ((forceShowSign) && (number > 0)) {
    result = `+${result}`;
  }

  return ((!result) || (result === '0')) ? def : result;
}

export function numberToBeauty(number) {
  number = getInt(number.toString());
  if (number < 1000) { return number; }
  if (number < 1000000) { return `${Math.floor(number / 1000)}k`; }

  return `${Math.floor(number / 1000000)}kk`;
}

export function parseTimeString(str, timeunits) {
  timeunits = (typeof Front !== 'undefined') ? Front.data.localizationStrings.timeunits.short : timeunits;

  let time = 0;
  let list = str.split(' ');
  _.each(list, (v) => {
    let cnt = getInt(v);
    let unit = v.replace(/([0-9]+)/g, '');

    let t = _.findKey(timeunits, (o) => { return o === unit; });
    if (Time.TIMEUNITS[t]) {
      time += cnt * Time.TIMEUNITS[t];
    }
  });

  return time * 1000;
}

export function trailZero(a) {
  if (a >= 10) {
    return a;
  }

  return `0${a}`;
}

export function transformHours(hours) {
  if(hours === Infinity) return 0;
  if (hours < 24) { return Math.round(hours) + ' ' + LANGUAGE.char_hour; }
  if (hours < 730) { return Math.round(hours / 24) + ' ' + LANGUAGE.char_day; }
  if (hours < 8765) { return Math.round(hours / 730) + ' ' + LANGUAGE.char_month; }

  return Math.round(hours / 8765) + ' ' + LANGUAGE.char_year;
}

export function secondToTime(sec) {
  let mins = Math.round(sec / 60);
  let hours = Math.floor(mins / 60);
  let minutes = Math.floor(mins % 60);

  return `${hours}:${trailZero(minutes)}`;
}

export function generateServerName(name){
  return `${getServerDomain()}_${getServerWorld()}_${name}`;
}

export function generateSubDomain(){
  return `https://${getServerWorld()}-${getServerDomain()}`;
}

export function generateDomain(){
  return `${generateSubDomain()}.ikariam.gameforge.com`;
}

export function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function addToLeftMenu(image, title, toBottom, quick_menu = false) {
  let $leftMenu = $('#leftMenu');
  if (!$leftMenu.length) {
    $leftMenu = $('<div id="leftMenu"></div>');
    $('#container').append($leftMenu);
  }

  let $js_viewCityMenu = $('#js_viewCityMenu');
  if (!$js_viewCityMenu.length) {
    $js_viewCityMenu = $('<div id="js_viewCityMenu" class="slot_menu city_menu"></div>');
    $leftMenu.append($js_viewCityMenu);
  }

  let $menuSlot = $js_viewCityMenu.find('.menu_slots');
  if (!$menuSlot.length) {
    $menuSlot = $('<ul class="menu_slots"></ul>');
    $js_viewCityMenu.append($menuSlot);
  }

  let quickMenu = quick_menu ? 'no-transition' : '';

  const Render = await import('./helper/templater.js');
  const tpl = await Render.default('utils-leftSlot', {index: $('li', $menuSlot).length, title: title, image: image, transition: quickMenu});
  let $li = $(tpl);

  if (toBottom) {
    $menuSlot.append($li);
  } else {
    $menuSlot.prepend($li);
  }

  return $li;
}

export async function addToSideBar(title, content, classes = '') {
  const Render = await import('./helper/templater.js');

  let $sideBar = $('#sidebar');
  const tpl = Render.default('utils-winSideBar', { title: title, content: content, classes: classes });
  const $window = $(tpl);

  if (!$sideBar.length) {
    $sideBar = $('<div id="sidebar" class="ikaeasy_sidebar_generated"><ul id="sidebarWidget"></ul></div>');
    $('#leftMenu').after($sideBar);
  }

  $sideBar.find('#sidebarWidget').append($window);

  $('.accordionItem .accordionTitle .indicator').off('click.ikaeasy').on('click.ikaeasy', function(){
    $(this).parent().toggleClass('active').next('.accordionContent').toggleClass('ikaeasy_toggle');
  });

  draggable($('.accordionTitle', $window), $sideBar);
}

export function draggable($el1, $el2, callback) {
  let draggable = {
    drag: false,
    mouseX: 0,
    mouseY: 0,
    sX: 0,
    xY: 0
  };

  let $doc = $(document);
  let addEvent = () => {
    let screenBorder = {
      right: $(window).width() - 20 - $el2.width(),
      bottom: $(window).height() - 20 - $el2.height()
    };

    $doc.off('.draggable').on('mousemove.draggable', (e) => {
      e.preventDefault();

      if (draggable.drag) {
        let top = draggable.sY + (e.pageY - draggable.mouseY);
        let left = draggable.sX + (e.pageX - draggable.mouseX);

        top = Math.min(Math.max(20, top), screenBorder.bottom);
        left = Math.min(Math.max(20, left), screenBorder.right);

        $($el2).css({
          right: 'auto',
          top: top,
          left: left
        });
      }
    });

    $doc.on('mouseup.draggable', (e) => {
      e.preventDefault();

      if (draggable.drag) {
        $doc.off('.draggable');
        callback && callback();
        draggable.drag = false;
      }
    });
  };

  $($el1).mousedown((e) => {
    e.preventDefault();

    draggable.drag = true;
    draggable.mouseX = e.pageX;
    draggable.mouseY = e.pageY;
    draggable.sX = $($el2).position().left;
    draggable.sY = $($el2).position().top;

    addEvent();
  });
}

export async function createDynamicWin(title, $content) {
  const Render = await import('./helper/templater.js');
  const tpl = await Render.default('utils-dynamicWindow', { title: title });

  const  $window = $(tpl);
  $('.dynamic', $window).append($content);

  return $window;
}

export function createDynamic(title, content) {
  let win = $('<ul id="sidebarWidget" style="width: 228px; "><li class="accordionItem"><a href="#toggle" tabindex="0" class="accordionTitle active" style="cursor:default !important;">' + title + '</a><div class="accordionContent"><div class="dynamic"></div></div></li></ul>');

  if (content) {
    $('.dynamic', win).append(content);
  }
  $('#sidebar').append(win);

  return win;
}

export function execute_js(code) {
  let msg = {
    type: 'FROM_IKAEASY_V3',
    cmd: 'code_eval',
    code: code
  };

  window.postMessage(msg, '*');
}

export function _modifyLANGUAGES () {
  if (typeof LANGUAGE.getLocalizedString !== 'function') {
    LANGUAGE.getLocalizedString = function(key, params) {
      let str = LANGUAGE[key];
      if ((typeof str !== 'string') || (!str)) {
        return key;
      }

      return _LanguageUtils.applyVariables(str, params);
    };
  }
}

export function sendNotification(title, body, params) {
  let pre = {
    domain: getServerDomain(),
    world: getServerWorld(),
    name: Front.data.serverName,
    lang: currentLanguage
  };

  if (params) {
    pre = _.merge(pre, params);
  }

  chrome.runtime.sendMessage({ cmd: 'notification', id: JSON.stringify(pre), requireInteraction: !!params, title: LANGUAGE.getLocalizedString(title), body: LANGUAGE.getLocalizedString(body) });
}


const _LanguageUtils = (function() {
  function getValue(obj, key, p, defValue) {
    let res = obj && obj[key];
    if (typeof res === 'function') {
      res = res.call(obj, p);
    }
    return (res == null) ? (defValue || null) : res;
  }

  return {
    applyVariables: function (str, params) {
      return str.replace(/\%(~?)(\w+)(?::(\w+))?\%/g, function(s, glob, key, p) {
        return getValue(params, key, p, s);
      });
    }
  };
})();
