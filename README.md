# CTC Local Easyconf for Home Assistant

Local integration for CTC heat pumps. It talks straight to the unit on your own
network and never touches myUplink or any other cloud.

Tested against a CTC EcoZenith i255 driving an EcoAir 720M, and a CTC EcoZenith
i550 Pro. It should also fit an i250, i350, i360, i555 Pro and EcoLogic, since
they share CTC's BMS register map.

## What it reads

The integration uses two transports at once, because neither alone gives the
whole picture.

**Modbus TCP, port 502.** The documented BMS interface. Around fifty readings,
polled continuously, with no side effects: outdoor and room temperature, flow
and return, heat pump in and out, hot gas and suction gas, high and low
pressure, brine, compressor speed, immersion heater power, phase currents,
degree minutes, energy counters and the status enumerations. This is also the
only safe way to control the unit.

**The display's web interface, port 80.** CTC's own screen mirror, the same
thing myUplink proxies for its remote view. It carries values the Modbus map
simply does not have. The most useful is delivered heat in kilowatts alongside
supplied power, which gives a real coefficient of performance, plus the
expansion valve position, superheat, evaporation and condensation in bar, and
the inverter's own voltages and currents.

**The coefficient of performance over a day, a week, a month, a year and the
lifetime.** Only the display counts delivered heat, on its history page, called
stored operation data on older display software, so that page has to be among
the harvested ones. The week and the month are read against the daily samples
the integration keeps for the year, so they appear a week and a month after it
was set up, long before the first yearly figure; the display's own "/30 dagar"
rows are not used, since they stand at zero on both units they have been read
from. The energy consumed comes from the display's own counter where it has
one. The older software has none, and there it comes from Modbus register 62341
instead, which
holds the same number: 9166 kWh against the display's 9166.0 on an i255. It is
read at the moment the display is, so the two always form a pair. The sensors
exist from the start, whatever Modbus has said so far, and their attributes say
what is going on: a register that answers zero is a counter standing at zero,
which is how a controller that never writes it is told from one that is simply
new, and a register that has not answered at all is said to be missing, rather
than the sensors never appearing because one block was silent in the first poll.
Each sensor stands on its own span and never borrows another's: the lifetime
figure is not relabelled as a year, the yearly sensor says how many of its 365
days of samples it has and from when, the first year is carried by the
commissioning day alone and says when that day is unknown, and when the display
goes quiet the figures that rest on stored samples stay available and say since
when it has been quiet, while the daily figure goes with the display.

A figure is shown as soon as it rests on something: ten kilowatt hours of
consumption for the lifetime and yearly ones, three for a single day, which is
where the display's whole kilowatt hours stop being mostly rounding. What is not
shown is a quotient outside 0.5 to 10, because that is not a performance figure
but two counters that do not belong together. When a figure is missing the sensor
says why in its attributes, under *skäl*: no sample old enough yet, too little
energy so far, a counter standing still although the unit has been running, or a
quotient that cannot be right. The sensor stays available and empty rather than
going unavailable, because an unavailable entity shows no attributes and the
reason is the point.

**Starts, run length and the mean run.** The status register says what the heat
pump is doing every half minute, and the integration watches it from one round
to the next: when the compressor last started, how many times it has started
since midnight, and how long the last run lasted. A defrost in the middle of a
run is part of the run, not a stop and a start. The codes that say nothing about
the compressor, a communication error among them, hold the last known state, so
an error that comes and goes is not counted as starts. The first round after a
start of Home Assistant is the baseline, and nothing is stored: the day's count
starts over with Home Assistant and says from when it counts. Where the history
page is harvested, the day's compressor minutes from Modbus divided by the
display's starts per day gives the mean run. The defrosts of an air to water
unit are counted the same way: how many since midnight, and when the last one
began, with its length and the outdoor temperature at the time as attributes,
which is where a healthy unit and an iced up one part ways. The defrost timer in
register 62137 is shown as the controller gives it; its unit is not confirmed.

**The transitions as events.** The same watch feeds an event entity, Händelser,
with the event types `kompressor_start`, `kompressor_stopp`, `avfrostning_start`,
`avfrostning_slut`, `larm`, `larm_borta`, `smartgrid_andrad` and
`systemstatus_andrad`. Each carries the code before and after, their labels, the
outdoor temperature and, for a stop or the end of a defrost, the length in
minutes. The logbook writes them out, and an automation triggers on the entity's
state instead of comparing states. The first round after a start of Home
Assistant raises none. None of this goes into the anonymous report.

**The alarm the panel shows, with its E-code.** Modbus says only that the heat
pump is off because of an alarm. The display prints the alarm itself out of its
own catalogue, `[E017] Givare solpaneler ut`, in the header icon at the top left
of a page and in the status field of a heat pump page, and the integration reads
those two places off the values it has already harvested: nothing is navigated
for it, nothing is polled faster, and the catalogue is never enumerated. The
sensor *Senaste larm* holds the latest alarm as the panel printed it, with the
code, the text, when it began, when it went away and the outdoor temperature at
the start as attributes, and the binary *Larm* carries the last ten episodes
under *episoder*. A sensor alarm such as E017 leaves the heat pump running, so
the binary, which reads the Modbus status, can be off while the sensor shows an
alarm. Where the panel prints the alarm has been seen on one page of one model
so far, the history page of an i550 Pro, and is to be confirmed the next time a
unit alarms; the status field is read only on a row captioned *Status*, so a page
of past alarms cannot pass one of them off as current.

## The catch with the display, and what the integration does about it

Only the page the panel is currently showing is kept up to date. Every other
screen returns a frozen snapshot from the last time it was rendered, and asking
for another page does not refresh it. Reading a different page means navigating
there, which moves the physical panel in your plant room.

So the display is treated as a supplement, not the base:

- Every page is ticked to begin with, and you choose which ones are worth the
  trip. The whole menu is kept, so a page can be switched on or off later under
  Configure without walking the panel again.
- A new version of the integration reads the menu again by itself, in the
  background: a newer parser understands rows and pages the older one passed
  over, and those are then read without anyone having to ask for it. A reading
  that lands while the panel is busy is tried again five minutes later, three
  times in all, and the log says so when they are spent.
- They are polled on a slow interval, thirty minutes by default.
- A restart of Home Assistant costs the panel nothing. The last harvest is
  kept, values and the moment each was read, so the sensors come up with it,
  and the next harvest is made when it would have been made anyway, one
  interval after the last one. Only an installation that has never harvested
  walks a few seconds after start. The serial number and the firmware versions
  are read in the background too, never during set-up.
- The panel is put back where it was afterwards.
- A walk asks the display for as little as it can. The operation data tile on
  the home screen is looked up once, between two pages the panel steps back to
  the operation data menu and replays the route from there instead of going
  home for each, and the serial number and firmware versions are read from
  the two screens they live on once those are known, rather than from every
  screen in the map. The log says on debug what each walk cost in requests
  and presses.
- If the panel is not where the integration left it, somebody is standing at it,
  and that cycle is skipped, with one page selected as much as with seven. Two
  cycles in a row are given way; the third is harvested anyway and the panel is
  put back on the page it was found on, so a panel left on another page does
  not stop the harvest for good. The first skip and the resumption are each one
  line in the log.
- The device's diagnostic sensor *Senaste displayskörd* says when the display
  was last read, and in its attributes how many harvests in a row were skipped
  or failed, when the next attempt is, the reason for the last empty one, and
  which pages were read and missed. The CTC page names a pump that Home
  Assistant is retrying, with the reason, instead of saying that nothing runs.
- Reading the menu again from Configure takes the same turn at the panel as
  the harvest and the walk to the system information page, so two of them can
  never walk it at once. Is the panel busy when you ask, the dialog says so and
  nothing is changed; ask again in a moment. The rest of the form is kept and
  saved with the pages you tick, and the three background tries start over, so
  a menu the background had given up on is read again after the save. A
  reading that misses writes neither the menu nor the version.

There is no second session to escape this with. The `/click2/` and `/scroll2/`
endpoints exist in the display's own JavaScript but the firmware answers 400 to
every form of them, on both models tested.

## Setup

1. On the panel, go to Installer, Define, Remote control and set **Ethernet** to
   **Modbus TCP**. The port row only appears once that is done. Note that this
   is reported to be mutually exclusive with the cloud connection.
2. Add the integration. It sweeps your local network and identifies CTC displays
   by asking each host for `/settings/name`. If yours is on another subnet, or
   the sweep finds nothing, type the address instead.
3. Tick the display pages you want harvested. The menu is read from the unit
   itself, so the list matches your model and your installed options, in your
   own language. Everything is ticked to begin with; switch off what you do not
   want. For the coefficient of performance, keep the page with the stored or
   historical operation data.

The serial number and the display's own software version are only written into
the System information page while that page is shown on the panel. The
integration walks there once to read them, and puts the panel back. It presses
nothing but Advanced, Display and System information, matched on the English
label so it works whatever language the panel is set to, checks where the panel
went after every press, and gives up at the first surprise. The service menu,
which holds a function test, a compressor quick start and reinstallation, is
never opened. Switch it off under Configure if you would rather open that page
yourself. What the walk finds goes straight into the device and its sensors;
nothing is reloaded for it.

When something is left for you to do, it is said in Home Assistant's repairs
view rather than only in the log: the page the coefficient of performance needs
is not harvested, the serial number has not been read yet, or a newer version
has been released. Home Assistant only offers updates for what HACS installed,
so a copy put into `custom_components` by hand is never offered one; the release
check asks GitHub once a day and can be switched off under Configure.

Everything the unit offers is created and switched on: every register, every
row of every harvested display page, and the controls. Nothing is written to the
heat pump by their existence alone, since a control mirrors the unit until
somebody sets it, and the page leaves out what this installation has only ever
reported as zero. Switch off what you do not want, per entity on the device page
or, for control as a whole, under the integration's options.

## The CTC page

Setting the integration up adds a **CTC** entry to the sidebar on its own.
There is nothing to configure and no button to press: the page is built the
moment it is opened, from the entities the integration has at that moment, so
it is never out of date. Each heat pump gets four tabs.

- **Overview.** What the pump is doing right now: the controller's status and
  the heat pump's own as a line of chips, the handful of controls worth reaching
  for, the circuit over the last day, and the key figures as large numbers.
- **Controls.** Everything writable, as the thing it is, a mode picked from a
  list, a setpoint slid or typed, grouped by what it does to the house rather
  than by entity domain: heating, hot water, operation and power. Letting go of
  every override at once has a section of its own.
- **Performance.** How the pump has run: the circuit and the compressor over a
  day, energy and the coefficient of performance per day over a month, and then
  every reading in its own section, temperatures, hot water, energy, the
  refrigerant circuit, the settings stored in the heat pump and what the unit
  says about itself.
- **All values.** The full list with a search field over it, every value the
  installation offers, the display's own pages among them under the names the
  panel prints. Nothing is left out here whatever it reads, which makes it the
  tab that answers whether a value exists at all.

The cards are the integration's own rather than Home Assistant's tiles, because
a heat pump has a hundred values and they have to fit on a screen: chips for the
status, a small name over a large number for the key figures, one row with the
control itself for anything writable, and everywhere else a dense list, name on
the left and value on the right, in as many columns as the screen has room for.

The display's own web interface is one click away from the device page, under
*Settings, Devices and services*: the device's link opens the panel's own page
in a new tab. It is not on the CTC page, because a browser will not
show an http page inside a Home Assistant reached over https, and because the
panel answers it from the same small web server the integration harvests from.

**Every value is explained.** Beside each one is a blue ⓘ, and it writes the
explanation out underneath: what the value is, where it comes from, a Modbus
register and whether it is a stored setting or a volatile control register, or
the display page and how often it is read, and a link to Home Assistant's own
dialog for the entity. The name explains itself when tapped too, and on hover,
and on a tile the icon still opens that dialog directly. The explanations come
from CTC's BMS manual and CTC's own description of the operation data rows, and
say so where a register's meaning is not confirmed on a running unit. They are
in Swedish, like the entity names.

**What is on the page is decided by your installation.** Heat pumps, indoor
units, software revisions and settings differ, and the controller answers for
hardware that is not fitted with a clean zero: a brine pump on an air to water
heat pump, current sensors that were never installed, the refrigerant circuit of
a unit whose compressor has never run. So a reading that your installation has
only ever reported as zero is left off, and it turns up the first time it has a
value. The integration remembers what it has seen, and the first time it runs it
looks through the past year of Home Assistant's own statistics, so a compressor
that is merely at rest right now is not mistaken for one that is missing. Status,
controls and the settings stored in the heat pump are shown whatever they read,
and so is everything on All values, which is the point of that tab.

A reading with nothing to show, such as a sensor CTC reports as not fitted, a
display page not reached yet or a yearly figure without a year of history, is
hidden until it has a value, and a section goes when all of its readings have.
Status and control are always shown. Entities you disable or hide are left off,
and a name you give an entity is the name on the page. So are values from a
display page that is no longer harvested, which stay in the entity registry as
unavailable until you delete them.

While the page is open it follows along: a reload of the integration, or an
entity enabled, disabled or renamed, makes it fetch the new layout by itself.
The page is read only, since the next opening would build it again anyway.

The explanations need the integration's card script, which is added as a
Lovelace resource on its own. A browser that already has Home Assistant open
loads resources once per page load, so **reload the page once after installing
or updating** the integration. Where resources are managed in
`configuration.yaml` the script is injected into the frontend instead; should
the page show errors for `ctc-ecozenith-tile` there, add
`/ctc_ecozenith/ctc-ecozenith-card.js` as a `module` resource yourself.

Home Assistant has no public call for adding a dashboard from an integration, so
the page is registered the way Lovelace registers a dashboard from
`configuration.yaml`, and checked before use. If a later Home Assistant moves
those parts, the integration logs it and runs on without the page. An address
that is already taken, `/ctc-ecozenith`, is left alone.

## Control

Control writes only to CTC's volatile 1000 block: maximum compressor speed,
immersion heater limits, room and hot water setpoints, hot water and price mode,
zone mode. Those registers are not stored in EEPROM, so they can be written as
often as needed, and the controller forgets them roughly five minutes after the
last write. That expiry is the safety net. If Home Assistant stops, the heat
pump quietly returns to its own settings.

A mode is released by choosing *Släpp styrningen*. A setpoint has no such
position, so **Släpp all styrning** stops every override at once: Home Assistant
stops writing, and the controller goes back to its own settings within about
five minutes.

What Home Assistant shows is what the controller holds. An override counts as
in force only from a write that actually reached the unit, each control entity
carries the attributes *senast skriven* and *gäller till*, and when no write has
reached the unit for five minutes the override is released on this side too,
with one line in the log, since the controller has forgotten it by then. A
released override is not taken up again by itself when the unit answers again:
the entity then shows the unit's own setting with *styrning aktiv: nej*, and
whoever set the override, a person or an energy manager, sets it anew. An
energy manager that reads the entity should treat that combination as a lost
command rather than as somebody's manual change.

The stored settings in the 61500 block are exposed read only and never written.
CTC states plainly that the number of write cycles there is limited and that
frequent writing can destroy the controller. That is enforced in code, not left
to convention: the Modbus client refuses any address outside the control
registers before it even takes the connection, and the test suite checks that a
write to 61500, 61503 or 62000 never reaches the wire.

## Known limits

- **One Modbus master.** The controller accepts a second TCP connection and then
  resets it as soon as that client sends anything, which looks exactly like the
  unit being offline. Do not point a second tool at it while this is running.
  That includes a `modbus:` block in `configuration.yaml` pointing at the same
  unit, which has to be removed before this integration can connect. The
  integration owns its own session accordingly: the library is told never to
  reconnect by itself, a client is closed before another is built, and a
  connection that is found gone is given up at once and knocked on again,
  after the controller's settle time, on the next poll.
- **A register the model lacks is answered with silence.** The controller
  simply does not reply, so such a block costs a full timeout every poll. After
  three polls in a row where a block stayed silent while the rest answered, it
  is left out until Home Assistant restarts, and the log says so once. A poll
  where nothing answers, or where the connection is lost halfway, ends there
  and the readings go unavailable, rather than waiting out every remaining
  block against a dead line.
- **CTC sets the pace.** The controller cannot pipeline and documents an update
  rate of one second, so requests are serialised, spaced out, and capped at a
  hundred registers each. It also needs a moment after the socket opens before
  it answers, so the first poll after a reconnect is delayed deliberately.
- **The web server drops connections above roughly five in flight.** The client
  keeps three.
- **A slow answer costs one reading, not all of them.** A reading that times out
  is asked for once more, with longer patience; a tap never is, since a tap that
  did land would move the panel twice. A harvest that fails keeps the readings it
  already had and tries again in five minutes instead of thirty. Only after three
  failed harvests in a row are the display's readings called unavailable. A
  single page that cannot be reached costs that page alone: its readings keep
  the moment they were last read, shown as the attribute *senast läst*, and go
  unavailable once that moment is three intervals old, while the other pages
  go on as usual.
- **Absent hardware still answers.** The controller replies for ten heat pumps
  and four heating systems whatever is actually installed, with plausible
  numbers. Readings marked as missing use CTC's own markers, plus or minus 9999
  and 10000 and 32767, and a 32 bit counter of all ones, which are filtered out
  after the sign is applied, so a negative marker cannot pass as a temperature.
  A display row that reads the marker and has never read as a number, the brine
  temperatures on an air to water unit for instance, gets no entity until it
  first does; the first harvest in which it reads as a number adds the entity,
  with no reload. A row that has read as a number once, a true zero included,
  keeps its entity through any spell of the marker, an outdoor unit switched
  off for a week among them. The integration remembers which rows have, beside
  what it has seen. An entity from an earlier version for a row the display's
  menu no longer has on its page is removed from the registry, with a line in
  the log; one for a row that is still on its page, or on a page the menu does
  not know, is left alone.
- **The web interface is undocumented.** A firmware update can change it. Modbus
  is documented and will keep working.
- **The coefficient of performance is untested on an i360.** Going by CTC's
  manual, its stored operation data page counts delivered energy but not the
  energy consumed, so the consumption comes from Modbus register 62341 there.
  That has not been confirmed on a running unit yet.

## Anonymous statistics

The integration sends one report per day to <https://stats.rnet.se>. The
client shared by the beolink integrations, `stats.py`, puts in it: a random id
created when the integration was installed, so that the daily reports can be
kept apart and erased on request (the server keeps it only as a hash, and it
says nothing about who you are), which
version of the integration you run, your Home Assistant version, installation
type and Python version, the language and the country you have set in Home
Assistant itself, an approximate position rounded to about 11 km, how many
entities and devices the integration created, a hash of Home Assistant's
instance id so that beolink integrations on the same installation can be seen
to be on the same installation (the id itself never leaves the house, and the
server hashes the hash again before storing it), how many warnings and errors
the integration's own logger wrote since the previous report, never a message,
and the config entry's state by Home Assistant's own name for it, such as
loaded or waiting to be set up again, never the reason.

This integration's own part, `stats_extra.py`, adds which transports are in
use, whether control is enabled, how many display pages are harvested and how
many register reads failed. It also says whether a coefficient of performance
is possible and, if not, why: whether the row with the unit's operating hours
was recognised on the history page, whether the delivered heat counter was
recognised there too, whether the energy consumed comes from the display or
from Modbus (for Modbus, whether register 62341 has
answered at all since Home Assistant started, judged on the raw words so that
CTC's marker for a counter that is not fitted still counts as an answer),
whether each of those two counters actually handed over a number at the last
read, and which of three things is in
the way, too little energy counted so far, a counter standing still although
the unit has been running, or a quotient no heat pump could produce. Yes or no
each time, never which page or what is on it.

It also sends what the installation is made of and how well it performs: the
indoor unit's model, the outdoor unit's model, the firmware in the display, in
the heat pump's control board and in the control unit, the week the machine was
built, and its coefficient of performance over the last day, over a rolling
year, over the machine's first year and over its lifetime. The figures over a
week and a month are not sent.

The flags and the numbers the report can carry are two closed lists next to
the code that builds it, `FEATURE_KEYS` and `METRIC_KEYS` in `stats_extra.py`,
and a test builds the report with everything set and fails the moment it grows
a key that is not on them. This text, the consent text under Configure and the
list at <https://stats.rnet.se/integritet> are kept to those lists, so the
promise and the report cannot drift apart unnoticed.

CTC writes a serial number as three groups of four digits: which product it is,
the year and week it was made, and a sequence number. The first two groups are
sent, because they describe a production run. **The sequence number, which is
what identifies your particular machine, is not sent.**

It never sends a name, an address, an exact position, a full serial number, an
entity name or a page name, and your IP address is not stored or used to guess
where you are. **One measurement can leave the house, and only when it is wrong:**
where a lifetime counter stands still although the unit has been running, or where
the two counters give a quotient that cannot be right, the two totals in kilowatt
hours are sent along with the flag. They are what separates a counter reading zero
from one that was never read, and without that nothing about such an installation
can be fixed. A machine that has merely not counted far enough yet sends no
numbers, only the flag saying so. The position is rounded inside your
own installation before anything is sent, and rounded again on the server, so a
finer value does not exist in the database. Reports are stored per date, never per
time of day, so they cannot show when anyone is home. What the backend accepts
is a closed list with a pattern per field, so free text cannot be stored even
by mistake. The statistics sit behind a login at <https://stats.rnet.se> and are
seen only by whoever runs the service.

The point is to know which versions are actually in the field, which parts are
worth maintaining and whether something is failing on units other than mine.

The first report after each start of the integration is also written out in
full to Home Assistant's log at info level, so you can read exactly what left
your installation before deciding whether to keep it on.

To opt out: *Settings, Devices and services, CTC Local Easyconf, Configure, Send
anonymous usage statistics.* Switching it off also erases what has already been
sent about your installation. The full list of fields and the reasoning:
<https://stats.rnet.se/integritet>. The code that builds the report is
`stats_extra.py`, and the client that sends it is `stats.py`.

## Tests

The suite in `tests/` runs without Home Assistant installed and is what CI
runs: `pip install -r requirements-test.txt` and `python -m pytest -q`. It
covers the protocol decoding, the menu reading, the dashboard layout and the
report, all against captured fixtures from the two units. Nothing in it talks
to a real heat pump or to anything outside the process: the one socket it opens
is in `tests/test_pymodbus_surface.py`, where a scripted controller listens on
loopback inside the test process so that the whole chain, from pymodbus's real
surface down to the register words, can be driven end to end.

`tests/test_homeassistant.py` is the one that needs more than the test
requirements. It drives the integration
through a real Home Assistant core, from set-up to reload to the `number`
service call that writes a control register, with both the Modbus client and
the display replaced by stand-ins, and it checks the four things a unit test
cannot: that a failed set-up closes its one Modbus socket, that a reload never
has two clients open at once, that a setpoint reaches the right register as the
right raw value and keeps being written until it is released, and that the menu
is tried three times in total across reloads, not three times per reload. It
needs `pytest-homeassistant-custom-component`, which pins one Home Assistant
release and everything that release depends on, so it belongs in a virtual
environment of its own rather than beside the ordinary suite's requirements.
It skips itself, with a note, where the plugin is missing:

```sh
pip install pytest-homeassistant-custom-component
python -m pytest -q tests/test_homeassistant.py
```

The whole suite runs in that environment too, with the same `python -m pytest
-q`. Two things make that so. The plugin's own autouse fixtures are async and
need pytest-asyncio in auto mode, or every test in the session errors at
set-up; `tests/conftest.py` switches the mode on wherever pytest-asyncio is
loaded, so there is nothing to pass by hand. And `tests/ha_stub.py`, which
stands in for Home Assistant when it is not installed, steps aside when it is:
the modules then load against the real classes, the keepalive tests record the
real timer helper instead of the stand-in's, and only the four tests that
build a coordinator directly skip themselves, since a real core's coordinator
wants a running instance behind it.

## Roadmap

- **`test_counters_going_backwards_fall_back_to_lifetime`** puts its sample 400
  days back, which the year window (`YEAR_MAX_DAYS = 380`) now refuses on its
  own, so the test passes without reaching the backwards guard it is named
  after. At 366 days it tests the guard again.

## Licence

Apache 2.0.
