/* CTC Local Easyconf: the page's cards, with an explanation for every value.
 *
 * Served by the integration at /ctc_ecozenith/ctc-ecozenith-card.js and loaded as a
 * Lovelace resource. The page itself is built in Python (dashboard_views.py) and uses
 * four cards from here, all of them drawn by hand rather than out of Home Assistant's
 * own tiles, because a heat pump has a hundred values and they have to fit on a screen:
 *
 *   custom:ctc-ecozenith-chips      what the pump is doing right now, a line of chips
 *   custom:ctc-ecozenith-readings   the key figures, a small name over a big number
 *   custom:ctc-ecozenith-controls   one row per control: name on the left, the thing
 *                                   it is on the right, a list, a slider or a field
 *   custom:ctc-ecozenith-rows       a list of values, name on the left and value on
 *                                   the right, in as many columns as there is room
 *                                   for, with headings and a filter over the lot
 *
 * Every value carries a blue "i" right after its name. It writes the explanation out
 * underneath, together with where the value comes from and a way into Home Assistant's
 * own dialog for the entity, which is what the NIBE page settled on. The explanation
 * is also the hover text, since a hover alone never reaches a phone or a screen reader.
 * Every string goes into the page as text, never as HTML.
 *
 * The pure helpers at the top of the closure are what decides a write, which
 * control a number gets and what a typed field is taken to say, and what a state
 * reads as when the page has a word of its own for it. They are loaded into node
 * by tests/test_card*.py, so the closure hands them over and stops before the
 * custom elements when there is no browser. Nothing is a global in either place:
 * the NIBE card declares the same names at its top level, and the two may well be
 * loaded on one page.
 */

(() => {
  const HIDDEN_STATES = new Set(["unavailable", "unknown"]);
  //: A number control with more steps than a slider has pixels is a lottery to
  //: aim at, so it gets a field to type in instead: a room setpoint in tenths
  //: of a degree from 10 to 30 is 200 steps on 130 pixels.
  const SLIDER_STEPS = 130;

  /* ---------------------------------------------------------- pure helpers */

  function withUnit(value, unit) {
    return unit ? `${value} ${unit}` : String(value);
  }

  /** Which control a number gets: a slider when its range has few enough steps
   *  to aim at, a field to type in otherwise. Asked from the entity's own min,
   *  max and step, so it has to wait for the first state: setConfig has no hass
   *  yet, and asking there made everything a field. */
  function widgetFor(min, max, step) {
    const size = Number(step) || 1;
    const steps = (Number(max) - Number(min)) / size;
    // NaN fails both comparisons, so a number without a range gets a field.
    return steps > 0 && steps <= SLIDER_STEPS ? "slider" : "field";
  }

  /** What a typed field holds: a finite number, or null when it holds nothing
   *  a pump should be sent. Number("") is 0, and for the compressor's top speed
   *  0 is a legal value that leaves the house cold until someone presses
   *  Release, so an emptied field must never turn into a write. */
  function parseFieldValue(text) {
    if (text === null || text === undefined) return null;
    const trimmed = String(text).trim();
    if (trimmed === "") return null;
    const value = Number(trimmed);
    return Number.isFinite(value) ? value : null;
  }

  //: The domains a control lives in. One of these in "unknown" is a control
  //: without a value yet, not a reading that is missing.
  const CONTROL_DOMAINS = new Set(["number", "select"]);

  /** The page's own word for a state, or undefined when Home Assistant's own
   *  wording is the right one. The words come out of the table the page sends
   *  as `states`, in the language the page is built in. A binary sensor's on
   *  and off are looked up by device class first ("problem_on") and then
   *  plainly ("on"): Home Assistant would word them in the user's language
   *  instead, and put "Not running" and "Off" beside Swedish labels. A control
   *  in "unknown" has no value yet (the hot water setpoint has no mirror
   *  register, so it is unknown until something is written) and says so
   *  rather than "Unknown". A 0 the pump means as no limit at all is a word
   *  too, for the keys the page marks with `zeroMeans`, and only for the
   *  pump's own value: a 0 somebody wrote through the control is a 0. An
   *  unknown that means nothing has happened yet (the last alarm before a
   *  panel has shown one) is a word as well, for the items the page marks
   *  with `unknownMeans`, so the full list does not read "Okänd" for months.
   *  An enum sensor's state is the integration's own string already and is
   *  left to Home Assistant, as is everything the table has no word for, so
   *  an older page without the table reads as it did. */
  function ownWord(entityId, stateObj, states, zeroMeans, unknownMeans) {
    if (!stateObj || !states) return undefined;
    const domain = String(entityId || "").split(".")[0];
    const state = stateObj.state;
    const attributes = stateObj.attributes || {};
    if (domain === "binary_sensor" && (state === "on" || state === "off")) {
      const deviceClass = attributes.device_class;
      return (deviceClass && states[`${deviceClass}_${state}`]) || states[state];
    }
    if (CONTROL_DOMAINS.has(domain) && state === "unknown") return states.unset;
    if (unknownMeans && state === "unknown") return states[unknownMeans];
    if (zeroMeans && parseFieldValue(state) === 0 && !controlActive(attributes)) {
      return states[zeroMeans];
    }
    return undefined;
  }

  /** Whether an override written from Home Assistant is in force on a control.
   *  The integration says it under the English `control_active`, a boolean,
   *  and under the Swedish `styrning aktiv`, "ja" or "nej", which goes in a
   *  later major version; an older integration sends only the Swedish one. */
  function controlActive(attributes) {
    const english = attributes.control_active;
    if (english !== undefined && english !== null) return english === true;
    return attributes["styrning aktiv"] === "ja";
  }

  if (typeof customElements === "undefined") {
    // Node, for tests/test_card*.py: hand over the helpers and stop here, before
    // anything below reaches for HTMLElement or document. A browser never takes
    // this branch, so the page gets no globals from it.
    if (typeof module !== "undefined") {
      module.exports = { widgetFor, parseFieldValue, ownWord, SLIDER_STEPS };
    }
    return;
  }

  /* ------------------------------------------------------------------ DOM */

  const STYLE = `
    /* A display rule of a card, or ha-card's own :host rule, would otherwise beat
       the browser's [hidden] and leave closed explanations on the page. */
    [hidden] { display: none !important; }
    :host { display: block; }
    ha-card { padding: 12px 16px; }
    .why {
      flex: none; background: none; border: none; padding: 0 2px; cursor: pointer;
      color: var(--primary-color, #03a9f4); font: inherit; line-height: 1;
    }
    .why:focus-visible {
      outline: 1px solid var(--primary-color, #03a9f4); outline-offset: 2px; border-radius: 50%;
    }
    .note {
      color: var(--secondary-text-color); font-size: .85em; line-height: 1.45; padding-top: 4px;
    }
    .note .meta { display: block; margin-top: 4px; font-size: .9em; opacity: .85; }
    .note a { color: var(--primary-color); cursor: pointer; text-decoration: underline; }
  `;

  function moreInfo(element, entityId) {
    element.dispatchEvent(
      new CustomEvent("hass-more-info", { bubbles: true, composed: true, detail: { entityId } })
    );
  }

  /** The blue "i" that opens an explanation. Every value the page shows has one:
   *  a value is only worth reading if you know what it is. */
  function explainButton(label, toggle) {
    const why = document.createElement("button");
    why.className = "why";
    why.type = "button";
    why.textContent = "ⓘ";
    why.setAttribute("aria-label", label || "Explanation");
    why.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      toggle();
    });
    return why;
  }

  /** Whether the user is holding this very control. The cards live in a shadow
   *  root, where the document's own idea of what has focus is the card itself,
   *  so the question has to be put to the root the control is in. */
  function holding(element) {
    const root = element.getRootNode();
    return Boolean(root) && root.activeElement === element;
  }

  /** What the cards have in common: the explanation, the state, the service call. */
  class CtcCard extends HTMLElement {
    constructor(style) {
      super();
      this.attachShadow({ mode: "open" });
      this._open = new Set();
      this._style = style;
    }

    setConfig(config) {
      this._config = config || {};
      this._build();
      if (this._hass) this._update();
    }

    set hass(hass) {
      this._hass = hass;
      this._update();
    }

    get hass() {
      return this._hass;
    }

    getCardSize() {
      return 2;
    }

    getGridOptions() {
      return { columns: "full", rows: "auto" };
    }

    _shell() {
      const style = document.createElement("style");
      style.textContent = `${STYLE}${this._style || ""}`;
      const card = document.createElement("ha-card");
      this.shadowRoot.replaceChildren(style, card);
      return card;
    }

    /** The name, the "i" after it, and the note the "i" opens. */
    _named(item, note, tag = "span") {
      const name = document.createElement(tag);
      name.className = "label";
      name.append(item.name || item.entity);
      if (item.explanation) {
        name.title = item.explanation;
        name.appendChild(
          explainButton(this._config.explain, () => this._toggle(item, note))
        );
      }
      return name;
    }

    _toggle(item, note) {
      const open = !this._open.has(item.entity);
      if (open) this._open.add(item.entity);
      else this._open.delete(item.entity);
      this._fill(note, item, open);
    }

    /** The explanation, then where the value comes from and a way to its dialog. */
    _fill(note, item, open) {
      note.hidden = !open;
      if (!open) return;
      note.replaceChildren();
      note.append(item.explanation || "");
      this._more(note, item);
      const meta = document.createElement("span");
      meta.className = "meta";
      if (item.source) meta.append(item.source, " · ");
      const link = document.createElement("a");
      link.textContent = this._config.more_info || "More info";
      link.setAttribute("role", "button");
      link.tabIndex = 0;
      const openDialog = (event) => {
        event.preventDefault();
        event.stopPropagation();
        moreInfo(this, item.entity);
      };
      link.addEventListener("click", openDialog);
      link.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") openDialog(event);
      });
      meta.appendChild(link);
      note.appendChild(meta);
    }

    /** What a card adds to a note between the explanation and the source:
     *  nothing, unless the card has something to say about this very value. */
    _more(note, item) {}  // eslint-disable-line no-unused-vars

    _state(entityId) {
      return this._hass ? this._hass.states[entityId] : undefined;
    }

    /** What a value reads as: the page's own word where it has one, else what
     *  Home Assistant makes of the state, else the state with its unit. */
    _text(item, stateObj) {
      if (!stateObj) return "";
      const own = ownWord(item.entity, stateObj, this._config.states, item.zero_means,
                          item.unknown_means);
      if (own !== undefined) return own;
      try {
        return this._hass.formatEntityState
          ? this._hass.formatEntityState(stateObj)
          : withUnit(stateObj.state, stateObj.attributes.unit_of_measurement);
      } catch (err) {
        return stateObj.state;
      }
    }

    _missing(item) {
      const stateObj = this._state(item.entity);
      if (!stateObj) return Boolean(item.hide_unavailable || item.show_reason);
      // R35: a value that can say why it is empty stays while merely unknown.
      if (item.show_reason) return stateObj.state === "unavailable";
      return Boolean(item.hide_unavailable) && HIDDEN_STATES.has(stateObj.state);
    }

    /** R35: the small line under a value: what the figure rests on, or, while
     *  there is no figure, why there is none. Both are attributes of the
     *  entity, named in the item as one name or a list of names, so an
     *  attribute the integration renames is still found by the page. */
    _below(item, stateObj) {
      if (!stateObj || (!item.sub && !item.reason)) return "";
      const attributes = stateObj.attributes || {};
      const first = (names) => {
        for (const name of [].concat(names || [])) {
          const found = attributes[name];
          if (found !== undefined && found !== null && String(found) !== "") return String(found);
        }
        return "";
      };
      if (HIDDEN_STATES.has(stateObj.state)) return first(item.reason) || first(item.sub);
      return first(item.sub);
    }

    /** The service call for a control, returned so the caller can see it
     *  refused: Home Assistant answers a value outside min and max, or a write
     *  the pump would not take, with a rejected promise and a toast of its own. */
    _call(entityId, value) {
      const domain = String(entityId).split(".")[0];
      if (domain === "select") {
        return this._hass.callService("select", "select_option", { entity_id: entityId, option: value });
      }
      if (domain === "number") {
        return this._hass.callService("number", "set_value", { entity_id: entityId, value: Number(value) });
      }
      if (domain === "button") {
        return this._hass.callService("button", "press", { entity_id: entityId });
      }
      return undefined;
    }
  }

  /* ------------------------------------------------------------------ chips */

  const CHIPS_STYLE = `
    .chips { display: flex; flex-wrap: wrap; gap: 8px; }
    .chip {
      display: flex; gap: 6px; align-items: baseline; padding: 6px 12px; border-radius: 999px;
      background: var(--secondary-background-color, #f1f3f4);
    }
    .chip .label { color: var(--secondary-text-color); font-size: .8em; }
    .chip .value { color: var(--primary-text-color); font-weight: 500; }
    .chips + .note { padding-top: 10px; }
    /* R36: a boolean chip is only on the line while it is on, in the theme's
       colour for what it means: an alarm red, a block or SmartGrid amber, a
       defrost blue, a running compressor or heater green. The alarm chip is a
       solid red pill with white text, the one thing on the page that must not
       be missed; the others are a tint of their colour with a dot in it, which
       is how Home Assistant's own tiles carry a state colour, and they stay
       legible in both themes. Without color-mix every one is a solid pill. */
    .chip[data-color="error"] { --chip-color: var(--error-color, #db4437); }
    .chip[data-color="warning"] { --chip-color: var(--warning-color, #ffa600); }
    .chip[data-color="info"] { --chip-color: var(--info-color, #039be5); }
    .chip[data-color="success"] { --chip-color: var(--success-color, #43a047); }
    .chip[data-color] { background: var(--chip-color); }
    .chip[data-color] .label, .chip[data-color] .value, .chip[data-color] .why {
      color: var(--text-primary-color, #fff);
    }
    @supports (background: color-mix(in srgb, red 24%, white)) {
      .chip[data-color]:not([data-color="error"]) {
        background: color-mix(in srgb, var(--chip-color) 22%, var(--card-background-color, #fff));
        box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--chip-color) 45%, transparent);
      }
      .chip[data-color]:not([data-color="error"])::before {
        content: ""; width: 8px; height: 8px; border-radius: 50%; flex: none;
        align-self: center; background: var(--chip-color);
      }
      .chip[data-color]:not([data-color="error"]) .label { color: var(--secondary-text-color); }
      .chip[data-color]:not([data-color="error"]) .value { color: var(--primary-text-color); }
      .chip[data-color]:not([data-color="error"]) .why { color: var(--primary-color, #03a9f4); }
    }
  `;

  class CtcEcoZenithChips extends CtcCard {
    constructor() {
      super(CHIPS_STYLE);
    }

    _build() {
      const card = this._shell();
      const strip = document.createElement("div");
      strip.className = "chips";
      const note = document.createElement("div");
      note.className = "note";
      note.hidden = true;
      this._items = (this._config.items || []).map((item) => {
        const chip = document.createElement("div");
        chip.className = "chip";
        // R36: the colour of what the chip means, from the page's YAML.
        if (item.color) chip.dataset.color = String(item.color);
        const value = document.createElement("span");
        value.className = "value";
        chip.append(this._named(item, note), value);
        strip.appendChild(chip);
        return { item, chip, value };
      });
      card.append(strip, note);
    }

    _update() {
      if (!this._hass) return;
      for (const row of this._items) {
        const stateObj = this._state(row.item.entity);
        // R36: a chip with an on_state is on the line only while it reads it,
        // so "Larm: OK" and "Avfrostning: Av" are not there to read.
        const off = row.item.on_state !== undefined
          && (!stateObj || stateObj.state !== String(row.item.on_state));
        row.chip.hidden = off || this._missing(row.item);
        // The chip is its name and then the page's word for the state. A
        // word that is the name over again is not written twice: an alarm
        // chip named "Larm" whose word for on is "Larm" reads "Larm" once,
        // the name being the message and the colour the state.
        const text = String(this._text(row.item, stateObj));
        const label = String(row.item.name || row.item.entity);
        const repeat = text.toLowerCase() === label.toLowerCase();
        row.value.textContent = repeat ? "" : text;
        row.value.hidden = repeat || text === "";
      }
    }
  }

  /* --------------------------------------------------------------- readings */

  /* The grids ask for a track no wider than the card: a bare minmax(140px, 1fr)
     still lays out a 140 px track inside a narrower card and sticks out of it,
     which on a 360 px phone is what the list did. min(140px, 100%) gives in. */
  const READINGS_STYLE = `
    .tiles { display: grid; gap: 14px 24px; grid-template-columns: repeat(auto-fill, minmax(min(140px, 100%), 1fr)); }
    .tile .label { display: block; color: var(--secondary-text-color); font-size: .8em; overflow-wrap: anywhere; }
    .tile .big { font-size: 1.6rem; font-weight: 500; color: var(--primary-text-color); }
    /* R35: what the figure rests on, or why there is none, in small text under it. */
    .tile .sub {
      color: var(--secondary-text-color); font-size: .75em; line-height: 1.3; overflow-wrap: anywhere;
    }
    .tile .sub:empty { display: none; }
    .tile .note { padding-top: 2px; }
  `;

  class CtcEcoZenithReadings extends CtcCard {
    constructor() {
      super(READINGS_STYLE);
    }

    _build() {
      const card = this._shell();
      const grid = document.createElement("div");
      grid.className = "tiles";
      this._items = (this._config.items || []).map((item) => {
        const tile = document.createElement("div");
        tile.className = "tile";
        const note = document.createElement("div");
        note.className = "note";
        note.hidden = true;
        const value = document.createElement("div");
        value.className = "big";
        tile.append(this._named(item, note, "div"), value);
        let sub = null;
        if (item.sub || item.reason) {
          // R35: the line under the figure, drawn from the entity's attributes.
          sub = document.createElement("div");
          sub.className = "sub";
          tile.appendChild(sub);
        }
        tile.appendChild(note);
        grid.appendChild(tile);
        return { item, tile, value, sub };
      });
      card.append(grid);
    }

    _update() {
      if (!this._hass) return;
      for (const row of this._items) {
        const stateObj = this._state(row.item.entity);
        row.tile.hidden = this._missing(row.item);
        row.value.textContent = this._text(row.item, stateObj);
        if (row.sub) row.sub.textContent = this._below(row.item, stateObj);
      }
    }
  }

  /* --------------------------------------------------------------- controls */

  const CONTROLS_STYLE = `
    .control {
      display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 4px 12px;
      padding: 8px 0; border-bottom: 1px solid var(--divider-color, #eee);
    }
    .control:last-of-type { border-bottom: none; }
    .control .label { color: var(--primary-text-color); overflow-wrap: break-word; min-width: 0; }
    .widget { display: flex; align-items: center; gap: 8px; justify-self: end; }
    .widget[data-pending="1"] { opacity: .5; }
    /* A control without a value yet: the slider's track dimmed with no knob on
       it, so nothing looks set, the "i" beside it, and a word in grey where the
       value would be. appearance: none is what lets the knob be hidden at all;
       the track is then drawn as a line of the card's own. */
    .widget[data-unset="1"] input[type="range"] {
      -webkit-appearance: none; appearance: none; height: 20px; opacity: .6;
      background: linear-gradient(var(--divider-color, #ccc), var(--divider-color, #ccc))
        center / 100% 4px no-repeat;
    }
    .widget[data-unset="1"] input[type="range"]::-webkit-slider-thumb {
      -webkit-appearance: none; appearance: none; width: 0; height: 0;
    }
    .widget[data-unset="1"] input[type="range"]::-moz-range-thumb {
      width: 0; height: 0; border: none; background: transparent;
    }
    .widget[data-unset="1"] .reading { color: var(--secondary-text-color); font-weight: 400; }
    select, input[type="number"] {
      font: inherit; padding: 6px 8px; border-radius: 8px; max-width: 190px;
      border: 1px solid var(--divider-color, #ccc);
      background: var(--card-background-color, #fff); color: var(--primary-text-color);
    }
    input[type="range"] { width: 130px; accent-color: var(--primary-color, #03a9f4); }
    .reading { color: var(--primary-text-color); font-weight: 500; min-width: 56px; text-align: right; }
    .widget button {
      font: inherit; padding: 6px 14px; border-radius: 8px; cursor: pointer;
      border: 1px solid var(--primary-color, #03a9f4);
      background: transparent; color: var(--primary-color, #03a9f4);
    }
    .note { grid-column: 1 / -1; }
  `;

  class CtcEcoZenithControls extends CtcCard {
    constructor() {
      super(CONTROLS_STYLE);
    }

    _build() {
      const card = this._shell();
      this._items = (this._config.items || []).map((item) => {
        const row = document.createElement("div");
        row.className = "control";
        const note = document.createElement("div");
        note.className = "note";
        note.hidden = true;
        const widget = document.createElement("span");
        widget.className = "widget";
        widget.dataset.pending = "0";
        row.append(this._named(item, note), widget, note);
        card.appendChild(row);
        return { item, row, widget, note, update: null };
      });
    }

    _update() {
      if (!this._hass) return;
      for (const row of this._items) {
        if (!row.update) {
          // The control is built from the entity's own range and options, so it
          // waits for the first state rather than guessing at an empty one.
          if (!this._state(row.item.entity)) continue;
          row.update = this._widget(row.widget, row.item, row.note);
        }
        row.update();
      }
    }

    /** A control without a value says why in its note: the pump does not give
     *  out its own value here, so nothing is set until Home Assistant writes. */
    _more(note, item) {
      const stateObj = this._state(item.entity);
      const states = this._config.states || {};
      if (!stateObj || stateObj.state !== "unknown" || !states.unset_note) return;
      const why = document.createElement("span");
      why.className = "meta";
      why.textContent = states.unset_note;
      note.appendChild(why);
    }

    /** Build the control itself and return how to keep it current. A control the
     *  user is holding is left alone: Home Assistant sends a new state while a
     *  slider is being dragged, and writing it back would fight the thumb. */
    _widget(container, item, note) {
      const entityId = item.entity;
      const domain = String(entityId).split(".")[0];
      /** Send a value and answer whether it was taken. A refused write is a
       *  rejected promise, and the reason is already on the screen as Home
       *  Assistant's toast. The row only has to stop looking busy, now rather
       *  than six seconds from now. No row of its own for the error, no range
       *  check and no clipping here: HA holds min and max, and clipping would
       *  send something other than what was typed. The promise wrapper also
       *  turns a call that throws outright into a refusal. The answer is a
       *  resolved true or false rather than a rejection, so a control with
       *  nothing to undo, a slider or a list, need not catch anything. */
      const send = (value) => {
        container.dataset.pending = "1";
        const settle = () => { container.dataset.pending = "0"; };
        const timer = setTimeout(settle, 6000);
        return new Promise((resolve) => resolve(this._call(entityId, value))).then(
          () => true,
          () => {
            clearTimeout(timer);
            settle();
            return false;
          }
        );
      };

      if (domain === "button") {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = this._config.press || "Press";
        button.addEventListener("click", () => send(null));
        container.appendChild(button);
        return () => {
          const stateObj = this._state(entityId);
          button.disabled = !stateObj || stateObj.state === "unavailable";
        };
      }

      if (domain === "select") {
        const select = document.createElement("select");
        select.addEventListener("change", () => send(select.value));
        container.appendChild(select);
        return () => {
          const stateObj = this._state(entityId);
          const options = (stateObj && stateObj.attributes.options) || [];
          if (select.options.length !== options.length ||
              [...select.options].some((option, i) => option.value !== options[i])) {
            select.replaceChildren();
            for (const option of options) {
              const choice = document.createElement("option");
              choice.value = option;
              choice.textContent = option;
              select.appendChild(choice);
            }
          }
          if (!holding(select) && stateObj) {
            select.value = stateObj.state;
            if (select.value === stateObj.state) container.dataset.pending = "0";
          }
          select.disabled = !stateObj || stateObj.state === "unavailable";
        };
      }

      const attributes = (this._state(entityId) || {}).attributes || {};
      const step = Number(attributes.step) || 1;
      const states = this._config.states || {};
      // A number without a value yet: the hot water setpoint has no mirror
      // register, so it is unknown until something is written. The browser
      // would draw its slider with the knob in the middle, as if someone had
      // set it there, and whoever dragged it wrote a value they never meant.
      // The row is marked unset instead: the style hides the knob and dims the
      // track, the reading says so, and an "i" beside it opens the row's note.
      const why = explainButton(this._config.explain, () => this._toggle(item, note));
      why.hidden = true;
      const mark = (unset) => {
        container.dataset.unset = unset ? "1" : "0";
        why.hidden = !unset;
      };
      // The card's YAML may insist on one or the other; otherwise the range decides.
      const kind = item.widget === "slider" || item.widget === "field"
        ? item.widget
        : widgetFor(attributes.min, attributes.max, attributes.step);
      if (kind === "slider") {
        const slider = document.createElement("input");
        slider.type = "range";
        slider.min = attributes.min;
        slider.max = attributes.max;
        slider.step = step;
        const reading = document.createElement("span");
        reading.className = "reading";
        slider.addEventListener("input", () => {
          // The user is setting it: whatever the pump says, it is no longer unset.
          mark(false);
          reading.textContent = withUnit(slider.value, attributes.unit_of_measurement);
        });
        /** Keep the slider current. `force` puts the pump's value in even while
         *  the user holds the slider: a range input keeps the focus after a
         *  drag, and a refused write must not leave the dragged value beside
         *  a knob the unset style has hidden. Taking the focus instead would
         *  pull it from a keyboard user in the middle of adjusting. */
        const update = (force = false) => {
          const stateObj = this._state(entityId);
          if (!stateObj) return;
          const unset = stateObj.state === "unknown";
          // A write on its way from an unset slider: the knob stays where the
          // user put it until the pump's value arrives or the refusal lets go.
          if (unset && container.dataset.pending === "1") return;
          if (force || !holding(slider)) {
            // Unset, the knob is parked at the bottom of the range, hidden by
            // the style, rather than left in the middle by the browser.
            slider.value = unset
              ? (attributes.min !== undefined ? attributes.min : 0)
              : stateObj.state;
            reading.textContent = this._text(item, stateObj);
          }
          // The pump reports what the slider shows: the write has landed, and
          // the row stops looking busy now rather than when the timer runs out.
          // Asked whoever is holding the slider, since a range input keeps the
          // focus after a drag, and asked of the numbers: Home Assistant writes
          // a float as "40.0" and a range input serializes it back as "40".
          // unknown and unavailable are NaN and never equal.
          if (Number(slider.value) === Number(stateObj.state)) container.dataset.pending = "0";
          mark(unset);
          slider.disabled = stateObj.state === "unavailable";
        };
        // A refused write goes straight back to what the pump says, unset
        // included, rather than waiting for the next state to come round, and
        // whether or not the slider still has the focus from the drag.
        slider.addEventListener("change", () => {
          send(slider.value).then((taken) => { if (!taken) update(true); });
        });
        container.append(slider, reading, why);
        return update;
      }

      const field = document.createElement("input");
      field.type = "number";
      if (attributes.min !== undefined) field.min = attributes.min;
      if (attributes.max !== undefined) field.max = attributes.max;
      field.step = attributes.step || "any";
      const unit = document.createElement("span");
      unit.className = "reading";
      unit.textContent = attributes.unit_of_measurement || "";
      // What the field last showed from the state, or last sent: a commit that
      // asks for the same thing again is a tab through the field, or a blur
      // after Enter, and must not become a second write to the pump.
      let shown = NaN;
      // The pump's own value back into the field, and counted as shown, so the
      // next blur has nothing new to send. With no state, or one the number
      // input will not hold (unavailable), the field ends up "", and shown has
      // to be NaN rather than Number("") = 0, or a 0 typed on purpose afterwards
      // would pass for already sent.
      const restore = () => {
        const stateObj = this._state(entityId);
        field.value = stateObj ? stateObj.state : "";
        const back = parseFieldValue(field.value);
        shown = back === null ? NaN : back;
      };
      const commit = () => {
        const value = parseFieldValue(field.value);
        if (value === null) {
          // Emptied, or holding something that is not a number: that asks for
          // nothing, so the pump's own value goes back in.
          restore();
          return;
        }
        if (value === shown) return;
        shown = value;
        send(value).then((taken) => {
          // Refused, and nothing shown or sent since: the refused number must
          // not count as shown, or Enter on it again would be dead. While the
          // field still shows it, the pump's own value goes back in, so a blur
          // sends nothing. A number the user has begun typing since is left
          // alone, and simply counts as new.
          if (taken || shown !== value) return;
          if (parseFieldValue(field.value) === value) restore();
          else shown = NaN;
        });
      };
      // Enter and leaving the field are the two ways of saying "this is it".
      // The browser's change event would also fire on every click of the
      // spinner arrows, one Modbus write per tenth of a degree.
      field.addEventListener("keydown", (event) => {
        if (event.key === "Enter") commit();
      });
      field.addEventListener("blur", commit);
      container.append(field, unit, why);
      return () => {
        const stateObj = this._state(entityId);
        if (!stateObj) return;
        const unset = stateObj.state === "unknown";
        if (!holding(field)) {
          field.value = stateObj.state;
          shown = Number(stateObj.state);
          if (String(field.value) === String(stateObj.state)) container.dataset.pending = "0";
        }
        // The number input holds "" for unknown; the placeholder says what that is.
        field.placeholder = unset && states.unset ? states.unset : "";
        mark(unset);
        field.disabled = stateObj.state === "unavailable";
      };
    }
  }

  /* ------------------------------------------------------------------- rows */

  const ROWS_STYLE = `
    .toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; padding-bottom: 10px; }
    .toolbar input {
      flex: 1; min-width: 0; max-width: 420px; padding: 8px 12px; font: inherit;
      border: 1px solid var(--divider-color, #ccc); border-radius: 8px;
      background: var(--card-background-color, #fff); color: var(--primary-text-color);
    }
    /* On a phone the field takes the whole line and the count goes under it. */
    @media (max-width: 480px) {
      .toolbar input { flex-basis: 100%; max-width: none; }
    }
    .count { color: var(--secondary-text-color); font-size: .9em; }
    .grid { display: grid; gap: 0 24px; grid-template-columns: repeat(auto-fill, minmax(min(320px, 100%), 1fr)); }
    .heading {
      grid-column: 1 / -1; font-size: .8rem; font-weight: 500; margin: 14px 0 4px;
      color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em;
    }
    .heading:first-child { margin-top: 0; }
    .row {
      display: grid; grid-template-columns: 1fr auto; gap: 2px 12px; align-items: baseline;
      padding: 9px 4px; border-bottom: 1px solid var(--divider-color, #eee);
      border-radius: 4px; cursor: pointer;
    }
    .row:hover, .row:focus-visible { background: var(--secondary-background-color, #f5f5f5); outline: none; }
    .row .label { color: var(--primary-text-color); overflow-wrap: anywhere; }
    .value { color: var(--primary-text-color); font-weight: 500; white-space: nowrap; text-align: right; }
    /* R35: what a figure rests on, or why there is none, under its row. */
    .row .sub { grid-column: 1 / -1; color: var(--secondary-text-color); font-size: .75em; line-height: 1.3; }
    .row .sub:empty { display: none; }
    .note { grid-column: 1 / -1; }
    .empty { color: var(--secondary-text-color); padding: 16px 4px; }
  `;

  class CtcEcoZenithRows extends CtcCard {
    constructor() {
      super(ROWS_STYLE);
      this._query = "";
    }

    setConfig(config) {
      if (!config || !Array.isArray(config.rows)) {
        throw new Error("ctc-ecozenith-rows needs rows");
      }
      super.setConfig(config);
    }

    getCardSize() {
      return 1 + this._items.filter((row) => !row.element.hidden).length;
    }

    _build() {
      const card = this._shell();
      if (this._config.filter) card.appendChild(this._searchField());
      const grid = document.createElement("div");
      grid.className = "grid";
      this._items = [];
      this._headings = [];
      for (const item of this._config.rows) {
        if (item && item.heading !== undefined) {
          const heading = document.createElement("div");
          heading.className = "heading";
          heading.textContent = item.heading;
          grid.appendChild(heading);
          this._headings.push({ element: heading, rows: [] });
          continue;
        }
        const row = this._row(item);
        grid.appendChild(row.element);
        this._items.push(row);
        if (this._headings.length) this._headings[this._headings.length - 1].rows.push(row);
      }
      this._empty = document.createElement("div");
      this._empty.className = "empty";
      this._empty.textContent = this._config.empty || "";
      this._empty.hidden = true;
      card.append(grid, this._empty);
    }

    _searchField() {
      const toolbar = document.createElement("div");
      toolbar.className = "toolbar";
      const input = document.createElement("input");
      input.type = "search";
      input.placeholder = this._config.filter === true ? "" : String(this._config.filter);
      input.setAttribute("aria-label", input.placeholder);
      input.value = this._query;
      input.addEventListener("input", () => {
        this._query = input.value.trim().toLowerCase();
        this._show();
      });
      this._count = document.createElement("span");
      this._count.className = "count";
      toolbar.append(input, this._count);
      return toolbar;
    }

    _row(item) {
      const element = document.createElement("div");
      element.className = "row";
      element.tabIndex = 0;
      element.setAttribute("role", "button");
      element.setAttribute("aria-expanded", "false");
      const note = document.createElement("div");
      note.className = "note";
      note.hidden = true;
      const value = document.createElement("span");
      value.className = "value";
      element.append(this._named(item, note), value);
      let sub = null;
      if (item.sub || item.reason) {
        // R35: the line under the row, drawn from the entity's attributes.
        sub = document.createElement("div");
        sub.className = "sub";
        element.appendChild(sub);
      }
      element.appendChild(note);
      const toggle = () => {
        this._toggle(item, note);
        element.setAttribute("aria-expanded", String(!note.hidden));
      };
      element.addEventListener("click", toggle);
      element.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          toggle();
        }
      });
      return { item, element, note, value, sub, seen: undefined, gone: false };
    }

    _update() {
      if (!this._hass) return;
      for (const row of this._items) {
        const stateObj = this._state(row.item.entity);
        row.gone = this._missing(row.item);
        // Only when the state itself changed: every update of any entity in
        // Home Assistant sets hass again.
        if (!stateObj || row.seen === stateObj) continue;
        row.seen = stateObj;
        row.value.textContent = this._text(row.item, stateObj);
        if (row.sub) row.sub.textContent = this._below(row.item, stateObj);
      }
      this._show();
    }

    /** What is on show: what has a value to show, and what the search asks for. */
    _show() {
      let shown = 0;
      for (const row of this._items) {
        const hidden = row.gone || !this._matches(row);
        row.element.hidden = hidden;
        if (hidden) row.note.hidden = true;
        else shown += 1;
      }
      // A heading with nothing under it says nothing.
      for (const heading of this._headings) {
        heading.element.hidden = !heading.rows.some((row) => !row.element.hidden);
      }
      if (this._count) this._count.textContent = `${shown} / ${this._items.length}`;
      if (this._empty) this._empty.hidden = !(this._config.filter && this._query && !shown);
    }

    /** A row is searched by everything it says: its name, its explanation, where
     *  the value comes from, and the value itself. */
    _matches(row) {
      if (!this._query) return true;
      const item = row.item;
      const haystack = [
        item.name, item.explanation, item.source, item.entity, row.value.textContent,
        row.sub ? row.sub.textContent : "",
      ].join(" ").toLowerCase();
      return this._query.split(/\s+/).every((word) => haystack.includes(word));
    }
  }

  const CARDS = {
    "ctc-ecozenith-chips": CtcEcoZenithChips,
    "ctc-ecozenith-readings": CtcEcoZenithReadings,
    "ctc-ecozenith-controls": CtcEcoZenithControls,
    "ctc-ecozenith-rows": CtcEcoZenithRows,
  };
  for (const [name, card] of Object.entries(CARDS)) {
    if (!customElements.get(name)) customElements.define(name, card);
  }
})();
