// SPDX-License-Identifier: MIT — winpodx.org client-side i18n.
// No trackers, no framework, no fetch — translations are bundled in
// lang/translations.js (window.WPX_I18N), so language switching works on
// file:// and https alike. English lives in the HTML and is snapshotted on
// load, so switching back to English restores cleanly.

(function () {
  "use strict";
  var LANGS = {
    en: "English", ko: "한국어", zh: "中文", ja: "日本語",
    de: "Deutsch", fr: "Français", it: "Italiano"
  };
  var KEY = "wpx_lang";
  var dicts = {};

  function snapshotEN() {
    var en = {};
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      en[el.getAttribute("data-i18n")] = el.textContent;
    });
    document.querySelectorAll("[data-i18n-html]").forEach(function (el) {
      en[el.getAttribute("data-i18n-html")] = el.innerHTML;
    });
    return en;
  }

  function applyLang(lang) {
    var dict = dicts[lang] || dicts.en;
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      var v = dict[el.getAttribute("data-i18n")];
      if (v != null) el.textContent = v;
    });
    document.querySelectorAll("[data-i18n-html]").forEach(function (el) {
      var v = dict[el.getAttribute("data-i18n-html")];
      if (v != null) el.innerHTML = v;
    });
  }

  function resolve() {
    var q = new URLSearchParams(location.search).get("lang");
    var stored = null;
    try { stored = localStorage.getItem(KEY); } catch (e) {}
    var nav = (navigator.language || "en").slice(0, 2).toLowerCase();
    var pick = q || stored || nav;
    return LANGS[pick] ? pick : "en";
  }

  function setLang(lang, persist) {
    if (!LANGS[lang]) lang = "en";
    document.documentElement.lang = lang;
    var sel = document.getElementById("lang-select");
    if (sel) sel.value = lang;
    if (persist) { try { localStorage.setItem(KEY, lang); } catch (e) {} }
    applyLang(lang);
  }

  function buildSelect() {
    var sel = document.getElementById("lang-select");
    if (!sel) return;
    Object.keys(LANGS).forEach(function (code) {
      var o = document.createElement("option");
      o.value = code; o.textContent = LANGS[code];
      sel.appendChild(o);
    });
    sel.addEventListener("change", function () { setLang(sel.value, true); });
  }

  function init() {
    dicts = { en: snapshotEN() };
    var bundle = window.WPX_I18N || {};
    Object.keys(bundle).forEach(function (code) { dicts[code] = bundle[code]; });
    buildSelect();
    setLang(resolve(), false);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else { init(); }
})();
