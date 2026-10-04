/* CTC: the manufacturer's own mark, as an icon the frontend can ask for by name.
 *
 * Home Assistant's sidebar takes an icon by name, and Material Design has no CTC
 * icon, so the integration brings one: the letters from CTC's own logo, as a single
 * path in the logo's own coordinates. It is registered the way an icon pack is, and
 * loaded on every page through the frontend's extra modules rather than as a Lovelace
 * resource, because the sidebar is drawn long before any dashboard is opened.
 *
 * An icon is monochrome by design: it takes the colour of whatever shows it, which is
 * why this is the letters alone and not the green square they sit on.
 */

(() => {
  const ICONS = {
    logo: {
      path: "M65.987 80.03c22.065 0 27.759 6.719 27.759 30.112v17.827H74.054v-14.583c0-10.767-.929-14.283-10.36-14.36h-5.3c-9.726 0-10.676 3.473-10.676 14.36v52.266c.022 10.489 1.034 13.928 10.36 14.003l5.299.001c9.62 0 10.655-3.398 10.676-14.004V148.74h19.693v19.799c0 23.394-5.694 30.111-27.759 30.111H54.599c-21.829 0-27.76-6.717-27.76-30.111v-58.397c0-23.393 5.931-30.112 27.76-30.112h11.388Zm129.414 0c21.844 0 27.644 6.585 27.758 29.415l.001 18.524h-19.692v-14.94c-.022-10.488-1.033-13.927-10.36-14.002l-5.299-.002c-9.727 0-10.677 3.474-10.677 14.36v52.267c.022 10.489 1.034 13.928 10.36 14.003l5.3.001c9.513 0 10.63-3.322 10.675-13.653l.001-17.263h19.692v19.799c0 23.394-5.695 30.111-27.759 30.111h-11.388c-21.828 0-27.759-6.717-27.759-30.111v-58.397c0-23.393 5.93-30.112 27.76-30.112H195.4Zm-6.342-33.459v18.993h-53.621v132.999H114.56V65.564H60.942V46.571h128.117Z",
      viewBox: "0 0 250 250",
    },
  };

  const icons = {
    getIcon: async (name) => ICONS[name] || ICONS.logo,
    getIconList: async () => Object.keys(ICONS).map((name) => ({ name })),
  };

  window.customIcons = window.customIcons || {};
  if (!window.customIcons.ctc) {
    window.customIcons.ctc = icons;
  }
})();
