/* Mount any of the page's four cards in node, on the stub DOM of card_dom.js.
 *
 * card_dom.js mounts the controls card around one number field and hands back
 * the levers to work it. The tests of the cards' words and layout need the
 * other cards too, and the controls card around a slider, so this file mounts
 * a card by its element name with a card configuration as dashboard_views.py
 * writes it and a hass with the states given. The stub DOM is the same one,
 * required here for the globals it installs.
 */

"use strict";

require("./card_dom.js");

/** The card as a browser would have it: configured, and given its first hass.
 *
 *  `states` is what hass.states holds, by entity id. `hass` may add to or
 *  override the hass object, for a formatEntityState of the test's own.
 */
function mountCard(cardPath, name, config, states = {}, hass = {}) {
  require(cardPath);
  const Card = customElements.get(name);
  if (!Card) throw new Error(`${name} is not defined by the card script`);
  const card = new Card();
  card.setConfig(config);
  card.hass = { states, callService: () => Promise.resolve(), ...hass };
  return card;
}

/** The text of the card's own <style>, the first thing in its shadow root. */
function styleOf(card) {
  const style = card.shadowRoot.children.find((e) => e.tagName === "style");
  if (!style) throw new Error("the card drew no style");
  return style.textContent;
}

/** Every element under the card that `test` accepts, depth first. */
function all(card, test) {
  const found = [];
  const walk = (node) => {
    for (const child of node.children) {
      if (test(child)) found.push(child);
      walk(child);
    }
  };
  walk(card.shadowRoot);
  return found;
}

module.exports = { mountCard, styleOf, all };
