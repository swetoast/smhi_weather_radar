# SMHI Weather Radar

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/swetoast/smhi_weather_radar/actions/workflows/validate.yml/badge.svg)](https://github.com/swetoast/smhi_weather_radar/actions/workflows/validate.yml)
[![Tests](https://github.com/swetoast/smhi_weather_radar/actions/workflows/tests.yml/badge.svg)](https://github.com/swetoast/smhi_weather_radar/actions/workflows/tests.yml)

Home Assistant integration for the SMHI Sweden composite weather radar. It shows the radar as two map images, reads precipitation at your location, and gives a short nowcast of when precipitation reaches you.

## Features

- National radar map and a local map around your location, on a dark basemap with faint borders and no city labels
- Home and people shown on both maps, people at home placed next to the home marker
- Optional animation of the last hour of frames
- Precipitation intensity and reflectivity at your location
- Nearest precipitation: distance, direction and the highest intensity nearby
- Nowcast: minutes until precipitation arrives and the highest intensity in the next hour, continued by the SMHI forecast when the radar sees nothing coming within the hour
- Precipitation type (rain, snow, sleet and more) from the SMHI point forecast, applied when the radar sees precipitation
- Thunder probability and expected precipitation for the next 24 hours from the same forecast
- SMHI weather warnings for rain, snow and thunder that cover your location
- Radar coverage: when a radar is out of service its area no longer reads as dry. Rain is traced into the gap from the last radar images where possible, the forecast covers the rest, and every location sensor says where its value came from
- Diagnostics download

## Requirements

- Home Assistant 2025.12 or newer
- A location inside SMHI radar coverage (Sweden and nearby areas). Setup stops with an error for locations outside it.

## Installation

### HACS

1. In HACS, open the menu in the top right and choose **Custom repositories**.
2. Add `https://github.com/swetoast/smhi_weather_radar` with the type **Integration**.
3. Search for **SMHI Weather Radar**, download it and restart Home Assistant.
4. Go to **Settings > Devices & services > Add integration** and pick **SMHI Weather Radar**.

### Manual

1. Copy `custom_components/smhi_radar` into the `custom_components` folder of your Home Assistant configuration.
2. Restart Home Assistant and add the integration as above.

## Configuration

Setup uses your Home Assistant location and needs no input. Everything else is under **Configure** on the integration:

| Option | Default | Description |
| --- | --- | --- |
| Update interval | 5 min | How often to check SMHI for a new frame. SMHI publishes one every 5 minutes. |
| Data freshness threshold | 15 min | Frame age at which **Radar data current** turns off. |
| Local map radius | 100 km | Distance from the location to the edge of the local map. |
| Nearby radius | 10 km | Radius used for **Precipitation nearby** and **Highest intensity nearby**. Drawn as a dashed ring on the local map. |
| People on the map | none | People shown at their GPS position. |
| Animate the last hour | off | Both maps loop the last hour of frames. The images get larger. |
| Use Home Assistant location | on | Turn off to pick a custom location on a map. |

## Entities

| Entity | Description |
| --- | --- |
| Weather radar | Image of the whole radar composite. |
| Local weather radar | Image of the area around the location. |
| Precipitation intensity | mm/h at the location. |
| Reflectivity | dBZ at the location. Disabled by default. |
| Observation time | Time of the newest radar frame. |
| Precipitation nearby | On when precipitation is within the nearby radius. |
| Highest intensity nearby | Highest mm/h within the nearby radius. |
| Nearest precipitation distance | Distance to the closest precipitation. |
| Nearest precipitation direction | Compass direction to the closest precipitation. |
| Precipitation expected in | Minutes until precipitation is expected within 3 km of the location. Counts down every minute between radar frames. 0 when it is already there. Beyond the radar's hour the SMHI forecast takes over (see below). |
| Highest intensity next hour | Highest mm/h expected within 3 km of the location in the next hour. |
| Precipitation type | What is falling now, from the SMHI forecast when the radar sees precipitation. |
| Radar data current | On while the newest frame is younger than the freshness threshold. |
| Radar coverage | `full` (the nearest radar works), `filled` (it does not, but a neighbouring radar reaches the location) or `gap` (no working radar reaches it). See [Radar coverage](#radar-coverage). |
| Thunder probability | Forecast thunderstorm probability for the current hour, in percent. |
| Precipitation next 24 hours | Expected precipitation in mm over the next 24 hours (forecast ensemble mean). |
| Weather warning | Highest SMHI warning level for rain, snow or thunder covering the location: `none`, `message`, `yellow`, `orange` or `red`. |

**Precipitation expected in** has these attributes:

| Attribute | Description |
| --- | --- |
| `source` | `radar` or `forecast`: where the arrival comes from. |
| `status` | Radar nowcast: `ok`, `no_rain` (no precipitation within about 160 km), `uncertain` (precipitation around but its movement is unclear) or `insufficient_history`. |
| `radar_horizon_minutes` | How far ahead the radar could follow the precipitation before running into a radar gap (60 without gaps). |
| `arrival` | Expected arrival time. |
| `forecast_probability`, `forecast_amount` | Probability (%) and expected mm of the forecast step, when the source is the forecast. |
| `radar_time` | Time of the radar frame the estimate is based on. |
| `motion_pairs` | Number of frame pairs that agreed on the movement. |
| `motion_speed_kmh`, `motion_heading`, `motion_heading_direction` | Movement of the precipitation. |
| `expected_type` | Forecast precipitation type at the arrival time. |
| `forecast` | Expected intensity every 5 minutes for the next hour. |

The state is unknown when neither the radar nor the forecast expects precipitation within 24 hours.

The other new sensors have these attributes:

| Sensor | Attributes |
| --- | --- |
| Thunder probability | `max_next_6h`, `max_next_6h_at`: highest probability in the next 6 hours and when. |
| Precipitation next 24 hours | `min`, `max`: the forecast range in mm. `max_probability`: highest chance of precipitation in the period. |
| Radar coverage | `offline_radars`: radars that are not delivering. `radars`: all SMHI radars, nearest first, with `name`, `distance_km`, `online` and `last_data`. |
| Precipitation intensity, Highest intensity nearby, Highest intensity next hour, Precipitation nearby | `source`: where the value came from, see below. |
| Weather warning | `warnings`: each warning with `event`, `level`, `description` (in the Home Assistant language), `area`, `start` and `end`. |

## How the nowcast works

The integration keeps the last hour of radar frames. It compares every pair of frames 10 to 30 minutes apart in a 320 km window around the location and estimates how the precipitation moves. Small cells grow and fade between frames, so the fields are log scaled and smoothed first, and anything that stays in place for the whole hour (ground clutter) is ignored. The movement is accepted when most of the frame pairs agree.

The newest frame is then moved forward minute by minute along that movement. The first minute with precipitation within 3 km of the location is the arrival.

Extrapolation cannot predict showers that form from nothing, so a sudden shower can still arrive unannounced.

When the radar sees nothing arriving within its hour, or cannot tell how the precipitation moves, **Precipitation expected in** uses the SMHI point forecast instead: the first forecast step after the radar's horizon with at least 50 % chance of precipitation. The forecast steps are an hour long at first, so forecast arrivals are less precise than radar ones.

## Radar coverage

SMHI's composite image is transparent both where a radar sees no precipitation and where no radar is measuring at all. When a radar is out of service its area therefore looks dry, with a straight or slightly curved edge where the neighbouring radars stop reaching.

The integration reads when each radar last delivered data (every 10 minutes). A pixel counts as measured when its nearest radar delivers, or another delivering radar is within about 140 km; beyond that a radar's lowest beam passes over low, steady rain. Unmeasured pixels are treated as unknown, never as dry.

Values at the location then come from the best source available, shown in each sensor's `source` attribute:

| Source | Meaning |
| --- | --- |
| `measured` | A working radar measures it. |
| `extrapolated` | Inside a gap: precipitation seen at the edge of the gap within the last 30 minutes, moved in along the measured motion. |
| `forecast` | Neither is possible: the SMHI point forecast for the current hour. Less precise, and it cannot tell where nearby precipitation is, so the nearest precipitation sensors stay unknown. |
| `radar` | Used by Precipitation expected in and Highest intensity next hour: the radar nowcast, including traced gap pixels. |

Reflectivity is only ever measured. The maps show the composite exactly as SMHI publishes it.

These choices come from simulations of a real outage (the Vara radar, October 2026) over 100 synthetic hours of moving, growing and decaying rain. Treating the gap as dry missed every case where it rained at the location. Tracing rain into the gap was right 87 % of the time with 20 minute old observations and 69 % with 60 minute old ones, so it is limited to 30 minutes. Combining tracing with the forecast found the arrival in 49 of 67 cases, against 14 when the gap was ignored.

## Forecast and warnings

The point forecast (SMHI snow1g) is fetched every 30 minutes and gives the precipitation type, thunder probability and the 24 hour precipitation. Weather warnings come from the SMHI impact-based warnings feed every 10 minutes. Only rain, snow and thunder warnings whose area contains the location are used. If either source is unavailable, its sensors become unknown and the radar keeps working.

## Data and credits

- Radar, radar status, forecast and warning data: [SMHI Open Data](https://www.smhi.se/data), licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- Radar site positions: WMO and OPERA radar databases.
- Basemap: [Natural Earth](https://www.naturalearthdata.com/) 1:10m, public domain.
- Marker glyphs: [Material Design Icons](https://pictogrammers.com/library/mdi/) by Pictogrammers, Pictogrammers Free License.
- Timestamp font: [Roboto](https://github.com/googlefonts/roboto), Apache License 2.0.

This project is not affiliated with SMHI.

## Development

Tests run against Home Assistant 2025.12:

```bash
pip install -r requirements_test.txt
pytest
```

`script/build_basemap.py` rebuilds `assets/basemap.json` from Natural Earth data.
