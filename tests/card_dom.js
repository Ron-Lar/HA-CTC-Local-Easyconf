/* A DOM just big enough to mount the controls card in node and work its number
 * field: focus, type, Enter, Tab, a new state from Home Assistant, a refusal.
 *
 * Loaded by tests/test_card_field.py before the card itself. It installs the
 * globals the card reaches for (HTMLElement, customElements, document) so the
 * card's closure takes its browser branch and defines the elements. Two things
 * are modelled with care because the field's logic turns on them: a number
 * input sanitizes anything that is not a number to "" the way a browser does
 * ("unavailable" included), and the root the controls live in has an
 * activeElement, which is what holding() asks about. Nothing is laid out or
 * drawn, and events are plain calls to the listeners.
 */

"use strict";

//: What a browser lets a number input hold: a valid floating-point number.
const NUMBER = /^-?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;

class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.dataset = {};
    this.listeners = {};
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.type = "";
    this._value = "";
  }

  get value() {
    return this._value;
  }

  set value(text) {
    const asText = String(text);
    const number = this.type === "number";
    this._value = number && asText !== "" && !NUMBER.test(asText) ? "" : asText;
  }

  appendChild(child) {
    if (child instanceof Element) {
      child.parentNode = this;
      this.children.push(child);
    } else {
      this.textContent += String(child);
    }
    return child;
  }

  append(...nodes) {
    for (const node of nodes) this.appendChild(node);
  }

  replaceChildren(...nodes) {
    this.children = [];
    this.append(...nodes);
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return name in this.attributes ? this.attributes[name] : null;
  }

  addEventListener(type, listener) {
    (this.listeners[type] = this.listeners[type] || []).push(listener);
  }

  /** Call the listeners for `type`, the way an event would reach them. */
  fire(type, event = {}) {
    for (const listener of this.listeners[type] || []) {
      listener({ type, target: this, preventDefault() {}, stopPropagation() {}, ...event });
    }
  }

  getRootNode() {
    let node = this;
    while (node.parentNode) node = node.parentNode;
    return node;
  }

  focus() {
    this.getRootNode().activeElement = this;
  }

  blur() {
    const root = this.getRootNode();
    if (root.activeElement === this) root.activeElement = null;
    this.fire("blur");
  }

  /** The first element under this one, depth first, that `test` accepts. */
  find(test) {
    for (const child of this.children) {
      if (test(child)) return child;
      const found = child.find(test);
      if (found) return found;
    }
    return null;
  }
}

class ShadowRoot extends Element {
  constructor(host) {
    super("#shadow-root");
    this.host = host;
    this.activeElement = null;
  }
}

class HTMLElement extends Element {
  constructor() {
    super("host");
  }

  attachShadow() {
    this.shadowRoot = new ShadowRoot(this);
    return this.shadowRoot;
  }
}

const registry = new Map();
globalThis.HTMLElement = HTMLElement;
globalThis.customElements = {
  get: (name) => registry.get(name),
  define: (name, element) => registry.set(name, element),
};
globalThis.document = { createElement: (tagName) => new Element(tagName) };

// The card arms a six-second timer per write. Node would wait it out before
// exiting, so the timer is unreferenced; clearTimeout still finds it.
const schedule = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms) => schedule(fn, ms).unref();

/** Mount the controls card with one number entity and hand back the levers.
 *
 *  Every service call stays open until the script answers it with accept() or
 *  refuse(), oldest first, so a refusal can arrive after the user has moved
 *  on. The states are those of a number entity as number.py publishes them:
 *  min, max and step from the register, a unit, and the value as text.
 */
function mount(cardPath, options = {}) {
  const entity = options.entity || "number.ctc_room_setpoint";
  const attributes = {
    min: 10, max: 30, step: 0.1, unit_of_measurement: "°C", ...(options.attributes || {}),
  };
  const item = { entity, name: "Field" };
  if (options.widget) item.widget = options.widget;

  require(cardPath);
  const Controls = customElements.get("ctc-ecozenith-controls");
  const card = new Controls();
  card.setConfig({ items: [item] });

  const calls = [];
  const open = [];
  const hass = (state) => ({
    states: state === null ? {} : { [entity]: { state, attributes } },
    callService: (domain, service, data) => {
      calls.push(data.value);
      return new Promise((resolve, reject) => open.push({ resolve, reject }));
    },
  });
  card.hass = hass(options.state === undefined ? "21.5" : options.state);

  const field = card.shadowRoot.find((e) => e.tagName === "input" && e.type === "number");
  if (!field) throw new Error("the card drew no number field");
  const tick = () => new Promise((resolve) => setImmediate(resolve));
  const answer = async (verdict) => {
    if (!open.length) throw new Error(`nothing was sent, so there is no call to ${verdict}`);
    const call = open.shift();
    if (verdict === "accept") call.resolve();
    else call.reject(new Error("refused"));
    await tick();
  };

  return {
    card, field, calls, open,
    focus: () => field.focus(),
    type: (text) => { field.value = text; },
    enter: () => field.fire("keydown", { key: "Enter" }),
    tab: () => field.blur(),
    state: (state) => { card.hass = hass(state); },
    accept: () => answer("accept"),
    refuse: () => answer("refuse"),
    tick,
    result: () => ({
      calls,
      field: field.value,
      open: open.length,
      pending: field.parentNode.dataset.pending,
    }),
  };
}

module.exports = { mount };
