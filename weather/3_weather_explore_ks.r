library(tidyverse)
library(arrow)

#### read in data ####

# hourly weather observations for PHL (station 72408013739) from NOAA ISD, 2017-2025

raw <- map_dfr(2017:2025, function(yr) {
  read_csv(
    paste0("https://www.ncei.noaa.gov/data/global-hourly/access/", yr, "/72408013739.csv"),
    col_types = cols(.default = col_character())
  )
})


#### extract daily summaries from SOD rows ####

# SOD (summary of day) rows hold the daily aggregates: snow depth, peak gusts, etc.
# those values don't appear anywhere in the hourly rows, so pulling them out before
# filtering. joined back by date at the end.
# note: OE wind speeds are in hundredths of m/s (not tenths like everything else)

sod_daily <- raw %>%
  filter(REPORT_TYPE == "SOD") %>%
  mutate(
    # KATIE: EDITED
    # I ran some code to check when the SOD daily summaries get released, and
    # each report (across all years/months/daylight savings transitions) 
    # is encoded to 04:59:00 (5a). To figure out why, I looked into NWS docs.
    # They report that daily summary messages are valid for the calendar day
    # ending at 23:59 LOCAL standard time. Philly is UTC - 5 -- so this tracks.
    
    # BUT (!) this means that:
    # A SOD report for July 1 2026 would show up at 2026-07-02T04:59:00
    # which means that when we pull the date_only (== 2026-07-02), and later
    # merge hourly observations to the dailies on date_only, we'd be matching 
    # 19 out of 24 hours a day to the wrong local date.
    
    # To address, I converted date_only below to UTC - 5 hours.
    # This pulls observations into the same UTC - 5 "day" and allows us to merge
    # on weather$date_only (hourlies) later on. Same fix is applied there.
    date_only = as.Date(ymd_hms(DATE, tz = "UTC") - hours(5)),
    # Before the final merge (at end of script), I also convert to EST since I
    # converted SEPTA time to Eastern in my first script (w/o thinking much of it)
    # and it'll make the weather -> full OTP merge easiest.
    daily_max_slp_hpa = na_if(as.numeric(str_split_fixed(MG1, ",", 4)[, 1]) / 10, 9999.9),
    daily_min_slp_hpa = na_if(as.numeric(str_split_fixed(MG1, ",", 4)[, 3]) / 10, 9999.9),
    daily_peak1min_ms = na_if(as.numeric(str_split_fixed(OE1, ",", 6)[, 3]) / 100, 999.99),
    daily_peak1min_dir = na_if(as.numeric(str_split_fixed(OE1, ",", 6)[, 4]), 999),
    daily_peak2min_ms = na_if(as.numeric(str_split_fixed(OE2, ",", 6)[, 3]) / 100, 999.99),
    daily_peak2min_dir = na_if(as.numeric(str_split_fixed(OE2, ",", 6)[, 4]), 999),
    daily_mean_wind_ms = na_if(as.numeric(str_split_fixed(OE3, ",", 6)[, 3]) / 100, 999.99),
    daily_snow_depth_cm = na_if(as.numeric(str_split_fixed(AJ1, ",", 6)[, 1]), 9999),
    daily_snow_24h_mm = na_if(as.numeric(str_split_fixed(AL1, ",", 4)[, 2]), 999),
    daily_snow_period_mm = na_if(as.numeric(str_split_fixed(AN1, ",", 4)[, 2]), 9999)
  ) %>%
  select(date_only, starts_with("daily_"))


#### filter to hourly observations ####

# FM-15: hourly METAR at :54 past the hour
# FM-16: special METAR, unscheduled, fires when conditions change fast
# excluding FM-12 (SYNOP), SOD/SOM/SY-MT (aggregates). FM-12 is only 13% of rows
# and uses different sensors/reporting conventions. keeping only the METAR stream.

weather <- raw %>%
  filter(REPORT_TYPE %in% c("FM-15", "FM-16"))

# static metadata columns, same for every row

weather <- weather %>%
  select(-STATION, -LATITUDE, -LONGITUDE, -ELEVATION, -NAME, -CALL_SIGN, -SOURCE, -QUALITY_CONTROL)

# these columns only had data on SOD/SOM/FM-12 rows so they're all NA now.
# the snow and wind peak ones already got pulled into sod_daily above.

weather <- weather %>%
  select(-AB1, -AD1, -AE1, -AK1, -AM1,
         -AJ1, -AL1, -AN1,
         -AH1, -AH2, -AH3, -AH4, -AH5, -AH6,
         -AI1, -AI2, -AI3, -AI4, -AI5, -AI6,
         -KB1, -KB2, -KB3, -KC1, -KC2, -KD1, -KD2, -KE1, -KG1, -KG2,
         -MG1, -MH1, -MK1,
         -OE1, -OE2, -OE3,
         -AT7, -AT8, -ED1, -GJ1, -GK1, -MV1,
         -KA1, -KA2)


#### parse fields ####

# every raw ISD field is a comma-separated string where missing data has a sentinel value
# (like 9999 for temp, 999999 for visibility). splitting out the relevant subfield and
# converting those sentinels to NA.

weather <- weather %>% mutate(
  temp_c = na_if(as.numeric(str_split_fixed(TMP, ",", 2)[, 1]) / 10, 999.9),
  dewpt_c = na_if(as.numeric(str_split_fixed(DEW, ",", 2)[, 1]) / 10, 999.9)
)

weather <- weather %>% mutate(
  wind_dir = na_if(as.numeric(str_split_fixed(WND, ",", 5)[, 1]), 999),
  wind_speed_ms = na_if(as.numeric(str_split_fixed(WND, ",", 5)[, 4]) / 10, 999.9)
)

# SLP often missing on FM-15/16, filled from MA1 below

weather <- weather %>% mutate(slp_hpa = na_if(as.numeric(str_split_fixed(SLP, ",", 2)[, 1]) / 10, 9999.9))

weather <- weather %>% mutate(
  ceiling_m = na_if(as.numeric(str_split_fixed(CIG, ",", 4)[, 1]), 99999),
  vis_m = na_if(as.numeric(str_split_fixed(VIS, ",", 4)[, 1]), 999999)
)

# two present weather sources: MW1 is a human observer, AW1 is the ASOS automated sensor.
# MW1 takes priority when both are present. the problem is AW1 miscodes light precip as mist
# (code 10) pretty regularly. MW1 would catch this but when AW1 fires alone the bad label sticks.
# fixed in the synthesis section below.
# one other gotcha: ASOS uses code 95 to mean "thunderstorm, no precip" but the same code in
# the WMO table (which MW1 uses) means "thunderstorm with rain". when code 95 came from AW1
# alone the label needs patching, otherwise it implies precip that isn't there.

wmo_labels <- c(
  "00" = "no significant weather", "01" = "clouds dissolving",
  "02" = "sky unchanged", "03" = "clouds developing",
  "04" = "smoke reducing visibility", "05" = "haze",
  "06" = "dust in suspension", "07" = "dust raised by wind",
  "08" = "dust/sand whirls", "09" = "duststorm within sight",
  "10" = "mist", "11" = "shallow fog patches",
  "12" = "shallow fog continuous", "13" = "lightning, no thunder",
  "14" = "precip not reaching ground", "15" = "precip in sight, distant",
  "16" = "precip in sight, near", "17" = "thunderstorm, no precip",
  "18" = "squalls", "19" = "funnel cloud",
  "20" = "drizzle (ceased)", "21" = "rain (ceased)",
  "22" = "snow (ceased)", "23" = "rain and snow (ceased)",
  "24" = "freezing rain (ceased)", "25" = "rain shower (ceased)",
  "26" = "snow shower (ceased)", "27" = "hail shower (ceased)",
  "28" = "fog (ceased)", "29" = "thunderstorm (ceased)",
  "30" = "slight duststorm, decreasing", "31" = "slight duststorm, steady",
  "32" = "slight duststorm, increasing", "33" = "severe duststorm, decreasing",
  "34" = "severe duststorm, steady", "35" = "severe duststorm, increasing",
  "36" = "drifting snow, low", "37" = "heavy drifting snow, low",
  "38" = "blowing snow", "39" = "heavy blowing snow",
  "40" = "fog at distance", "41" = "fog patches",
  "42" = "fog, sky visible, thinning", "43" = "fog, sky obscured, thinning",
  "44" = "fog, sky visible", "45" = "fog, sky obscured",
  "46" = "fog, sky visible, thickening", "47" = "fog, sky obscured, thickening",
  "48" = "freezing fog, sky visible", "49" = "freezing fog, sky obscured",
  "50" = "slight intermittent drizzle", "51" = "slight continuous drizzle",
  "52" = "moderate intermittent drizzle", "53" = "moderate continuous drizzle",
  "54" = "heavy intermittent drizzle", "55" = "heavy continuous drizzle",
  "56" = "slight freezing drizzle", "57" = "moderate/heavy freezing drizzle",
  "58" = "slight drizzle and rain", "59" = "moderate/heavy drizzle and rain",
  "60" = "slight intermittent rain", "61" = "slight continuous rain",
  "62" = "moderate intermittent rain", "63" = "moderate continuous rain",
  "64" = "heavy intermittent rain", "65" = "heavy continuous rain",
  "66" = "slight freezing rain", "67" = "moderate/heavy freezing rain",
  "68" = "slight rain and snow", "69" = "moderate/heavy rain and snow",
  "70" = "slight intermittent snow", "71" = "slight continuous snow",
  "72" = "moderate intermittent snow", "73" = "moderate continuous snow",
  "74" = "heavy intermittent snow", "75" = "heavy continuous snow",
  "76" = "ice crystals", "77" = "snow grains",
  "78" = "snow crystals", "79" = "ice pellets",
  "80" = "slight rain shower", "81" = "moderate/heavy rain shower",
  "82" = "violent rain shower", "83" = "slight rain and snow shower",
  "84" = "moderate/heavy rain and snow shower", "85" = "slight snow shower",
  "86" = "moderate/heavy snow shower", "87" = "slight snow pellet shower",
  "88" = "moderate/heavy snow pellet shower", "89" = "hail shower",
  "90" = "thunderstorm, no precip", "91" = "thunderstorm with slight rain",
  "92" = "thunderstorm with moderate/heavy rain", "93" = "thunderstorm with slight hail",
  "94" = "thunderstorm with heavy rain", "95" = "thunderstorm, slight/moderate",
  "96" = "thunderstorm with hail", "97" = "heavy thunderstorm",
  "98" = "thunderstorm with duststorm", "99" = "heavy thunderstorm with hail"
)

weather <- weather %>% mutate(
  wx_code = coalesce(
    na_if(str_split_fixed(MW1, ",", 2)[, 1], ""),
    na_if(str_split_fixed(AW1, ",", 2)[, 1], "")
  ),
  present_weather = case_when(
    is.na(na_if(str_split_fixed(MW1, ",", 2)[, 1], "")) & wx_code == "95" ~ "thunderstorm, no precip",
    TRUE ~ wmo_labels[wx_code]
  )
)

# slots 2/3/4 are always AW sensors so code 95 always means TS no precip there too

weather <- weather %>% mutate(
  wx_code_2 = na_if(str_split_fixed(AW2, ",", 2)[, 1], ""),
  present_weather_2 = case_when(wx_code_2 == "95" ~ "thunderstorm, no precip", TRUE ~ wmo_labels[wx_code_2]),
  wx_code_3 = na_if(str_split_fixed(AW3, ",", 2)[, 1], ""),
  present_weather_3 = case_when(wx_code_3 == "95" ~ "thunderstorm, no precip", TRUE ~ wmo_labels[wx_code_3]),
  wx_code_4 = na_if(str_split_fixed(AW4, ",", 2)[, 1], ""),
  present_weather_4 = case_when(wx_code_4 == "95" ~ "thunderstorm, no precip", TRUE ~ wmo_labels[wx_code_4])
)

# OC1 and OD1 are actually two separate gust instruments, not the same reading stored twice.
# they average about 1.6 m/s apart. using OC1 for gust_ms and OD1 field 5 for direction.
# ~3000 rows have OD1 but not OC1, so gust_ms is NA on those even though a direction was recorded.

weather <- weather %>% mutate(
  gust_ms = na_if(as.numeric(str_split_fixed(OC1, ",", 2)[, 1]) / 10, 999.9),
  gust_dir = na_if(as.numeric(str_split_fixed(OD1, ",", 5)[, 5]), 999),
  ice_accretion_cm = na_if(as.numeric(str_split_fixed(WA1, ",", 4)[, 2]), 999)
)

# MA1 fills the SLP gaps on FM-15/16, jumps coverage from ~7550 to ~9355 rows/year

weather <- weather %>% mutate(
  altimeter_hpa = na_if(as.numeric(str_split_fixed(MA1, ",", 4)[, 1]) / 10, 9999.9),
  slp_hpa = coalesce(slp_hpa, na_if(as.numeric(str_split_fixed(MA1, ",", 4)[, 3]) / 10, 9999.9))
)

# 3-hour pressure tendency. shows up on some FM-15/16 rows too (~26% coverage on METAR-only data).
# tendency code: 0-3 rising, 5 steady, 6-8 falling, 9 missing
# change field is always positive (direction is in the code)

weather <- weather %>% mutate(
  pressure_tendency = na_if(as.integer(str_split_fixed(MD1, ",", 6)[, 1]), 9L),
  pressure_3hr_change_hpa = na_if(as.numeric(str_split_fixed(MD1, ",", 6)[, 3]) / 10, 99.9)
)

# precip_trace = TRUE means trace detected but too small to measure (condition code 2).
# those rows have precip_mm = 0, not NA.

weather <- weather %>% mutate(
  precip_mm = na_if(as.numeric(str_split_fixed(AA1, ",", 4)[, 2]) / 10, 999.9),
  precip_period_hrs = as.numeric(str_split_fixed(AA1, ",", 4)[, 1]),
  precip_trace = if_else(is.na(AA1), NA, str_split_fixed(AA1, ",", 4)[, 3] == "2")
)

# AA2 mostly 6-hour accumulations, AA3 mostly 24-hour

weather <- weather %>% mutate(
  precip_mm_2 = na_if(as.numeric(str_split_fixed(AA2, ",", 4)[, 2]) / 10, 999.9),
  precip_mm_3 = na_if(as.numeric(str_split_fixed(AA3, ",", 4)[, 2]) / 10, 999.9)
)

# cloud coverage in oktas (0 = clear, 8 = overcast, 9 = sky obscured), height in meters

weather <- weather %>% mutate(
  cloud_cover_1 = na_if(as.numeric(str_split_fixed(GA1, ",", 6)[, 1]), 99),
  cloud_base_m_1 = na_if(as.numeric(str_split_fixed(GA1, ",", 6)[, 3]), 99999),
  cloud_cover_2 = na_if(as.numeric(str_split_fixed(GA2, ",", 6)[, 1]), 99),
  cloud_base_m_2 = na_if(as.numeric(str_split_fixed(GA2, ",", 6)[, 3]), 99999),
  cloud_cover_3 = na_if(as.numeric(str_split_fixed(GA3, ",", 6)[, 1]), 99),
  cloud_base_m_3 = na_if(as.numeric(str_split_fixed(GA3, ",", 6)[, 3]), 99999)
)

# AU is a separate ASOS precip classifier that gives more detail than the WMO codes.
# distinguishes drizzle, ice pellets, snow pellets, freezing rain, etc. separately.
# the type label is in field 3 of the 7-field AU string.

au_precip_labels <- c(
  "00" = "none", "01" = "drizzle", "02" = "rain",
  "03" = "snow/sleet", "04" = "snow grains", "05" = "ice crystals",
  "06" = "ice pellets", "07" = "hail", "08" = "snow pellets",
  "09" = "freezing rain/drizzle"
)

weather <- weather %>% mutate(
  precip_type = au_precip_labels[na_if(str_split_fixed(AU1, ",", 7)[, 3], "")],
  precip_type_2 = au_precip_labels[na_if(str_split_fixed(AU2, ",", 7)[, 3], "")],
  precip_type_3 = au_precip_labels[na_if(str_split_fixed(AU3, ",", 7)[, 3], "")]
)


#### tidy up column order ####

weather <- weather %>% relocate(
  temp_c, dewpt_c,
  wind_dir, wind_speed_ms, gust_ms, gust_dir,
  slp_hpa, altimeter_hpa, pressure_tendency, pressure_3hr_change_hpa,
  ceiling_m, vis_m,
  cloud_cover_1, cloud_base_m_1, cloud_cover_2, cloud_base_m_2, cloud_cover_3, cloud_base_m_3,
  precip_mm, precip_period_hrs, precip_trace,
  precip_mm_2, precip_mm_3,
  present_weather, wx_code,
  present_weather_2, wx_code_2,
  present_weather_3, wx_code_3,
  present_weather_4, wx_code_4,
  ice_accretion_cm,
  precip_type, precip_type_2, precip_type_3,
  .after = REPORT_TYPE
)


#### join daily summaries ####

weather <- weather %>%
  # KATIE EDITED: 
  # Adjusted here to convert to UTC - 5 again, so hourly-daily merge agrees
  mutate(date_only = as.Date(ymd_hms(DATE, tz = "UTC") - hours(5))) %>%
  left_join(sod_daily, by = "date_only") %>%
  select(-date_only)


#### quality check ####

# rows where a QC flag indicates suspect or erroneous data (flags 2, 3, 6, 7).
# ~61 flagged fields in the full 2017-2025 dataset, mostly on FM-16 rows.

suspects <- weather %>%
  mutate(
    qc_temp = str_split_fixed(TMP, ",", 2)[, 2],
    qc_dew = str_split_fixed(DEW, ",", 2)[, 2],
    qc_wdir = str_split_fixed(WND, ",", 5)[, 2],
    qc_wspd = str_split_fixed(WND, ",", 5)[, 5],
    qc_slp = str_split_fixed(SLP, ",", 2)[, 2],
    qc_ceil = str_split_fixed(CIG, ",", 4)[, 2],
    qc_vis = str_split_fixed(VIS, ",", 4)[, 2]
  ) %>%
  select(DATE, REPORT_TYPE, starts_with("qc_")) %>%
  pivot_longer(starts_with("qc_"), names_to = "field", values_to = "flag") %>%
  filter(flag %in% c("2", "3", "6", "7")) %>%
  mutate(field = str_remove(field, "qc_"))

suspects

glimpse(weather)
summary(weather %>% select(temp_c, dewpt_c, wind_dir, wind_speed_ms, slp_hpa, ceiling_m, vis_m))


#### codebook: raw source columns ####

# DATE: observation datetime
# REPORT_TYPE: FM-15 (hourly METAR), FM-16 (special METAR)
#
# TMP: temperature string, format: tenths_C, quality
# DEW: dew point, same format as TMP
# WND: wind, format: direction, direction_quality, obs_type, speed_tenths_ms, speed_quality
# SLP: sea level pressure, format: tenths_hPa, quality. often missing on FM-15/16.
# CIG: ceiling height, format: meters, quality, how_determined, CAVOK_flag
# VIS: visibility, format: meters, quality, variability_code, variability_quality
#
# AA1: liquid precip primary slot, format: period_hours, depth_tenths_mm, condition, quality
#   condition 2 = trace amount
# AA2: secondary accumulation slot, mostly 6-hour
# AA3: tertiary slot, mostly 24-hour
#
# AW1: ASOS automated present weather, 2-digit code and quality.
# AW2, AW3, AW4: second through fourth simultaneous ASOS conditions
# AW5: fifth simultaneous ASOS condition, not translated to text, decoded directly in synthesis.
#   ASOS code 95 = TS no precip, WMO code 95 = TS slight/moderate (different meanings)
# AU1: ASOS weather in 7-field format. field 3 is precip type.
# AU2, AU3: second and third simultaneous AU conditions
# AT1-AT8: automated past weather codes. no data at PHL.
# AX1-AX4: automated past weather, alternate encoding. no data at PHL.
# MW1: human observer present weather. same WMO code scale as AW1. takes priority over AW1.
#   also co-occurs with AW1 on FM-15/16 when a human augments the automated report.
# MW2: second simultaneous manual condition
# MW3: third simultaneous manual condition, not translated to text, decoded directly in synthesis.
#
# GA1: lowest cloud layer, format: coverage_oktas, quality, height_m, height_quality, type, type_quality
# GA2: second cloud layer
# GA3: third cloud layer
# GD1-GD4: sky cover summation layers
# GE1: convective clouds only. present on many rows but mostly empty within.
# GF1: overall sky condition summary. total coverage subfield mostly missing.
#
# MA1: altimeter and SLP, format: altimeter_tenths_hPa, quality, slp_tenths_hPa, quality
#   the SLP subfield here fills the gaps in the main SLP column (~60% -> ~99% coverage)
# MD1: 3-hour pressure tendency, format: tendency_code, quality, change_tenths_hPa, quality, ...
# MF1: station pressure. not reported by ASOS at PHL, always empty.
#
# OC1: peak instantaneous gust speed, format: speed_tenths_ms, quality
# OD1: separate gust observation with direction, format: type, period_minutes, speed_tenths_ms, quality, direction
#   different measurement from OC1 (mean 1.6 m/s apart when both present). gust_ms uses OC1, gust_dir uses OD1 field 5.
#   3,038 rows have OD1 but not OC1 (gust_ms NA on those).
#
# WA1: ice accretion, format: source_code, thickness_cm, rate_code, quality. very rare.
# RH1-RH3: relative humidity over a period. not reported by ASOS at PHL, always empty.
# REM: free text remarks with the raw SYNOP or METAR string
# EQD: audit trail of any data corrections applied


#### synthesize wx_primary and wx_secondary ####

# the goal is to collapse the present weather info into two clean columns:
#   wx_primary: one dominant condition per row (rain, snow, fog, thunderstorm, etc.)
#   wx_secondary: anything notable happening alongside it (e.g. fog during rain, TS during snow)
#
# the problem is the raw data has up to 10 weather columns from three different sources:
#   AW1-AW5: ASOS automated sensor, up to 5 simultaneous conditions
#   MW1-MW3: human observer using WMO codes
#   AU1-AU3: separate ASOS precip type classifier
# slots 1-4 are already translated to text labels above. AW5 and MW2/MW3 aren't, so
# decoded directly from their numeric codes here.

# maps a present_weather text string to a bucket
classify_wx <- function(pw) {
  case_when(
    pw == "heavy thunderstorm" ~ "thunderstorm_heavy",
    pw == "thunderstorm with hail" ~ "thunderstorm_hail",
    pw %in% c("thunderstorm with moderate/heavy rain",
               "thunderstorm, slight/moderate") ~ "thunderstorm_moderate_rain",
    pw == "thunderstorm with slight rain" ~ "thunderstorm_slight_rain",
    pw == "thunderstorm, no precip" ~ "thunderstorm_only",
    str_detect(coalesce(pw, ""), "hail") ~ "hail",
    pw %in% c("moderate/heavy freezing rain",
               "slight freezing rain") ~ "freezing_rain",
    pw == "slight rain and snow" ~ "mixed_rain_snow",
    pw %in% c("heavy continuous rain",
               "heavy intermittent rain") ~ "heavy_rain",
    pw %in% c("moderate continuous rain",
               "moderate intermittent rain") ~ "moderate_rain",
    pw == "slight continuous rain" ~ "slight_rain",
    pw %in% c("heavy continuous drizzle",
               "heavy intermittent drizzle") ~ "heavy_drizzle",
    pw %in% c("slight continuous drizzle",
               "moderate continuous drizzle",
               "moderate intermittent drizzle") ~ "slight_drizzle",
    pw %in% c("heavy continuous snow",
               "heavy intermittent snow") ~ "heavy_snow",
    pw %in% c("moderate continuous snow",
               "moderate intermittent snow") ~ "moderate_snow",
    pw == "slight continuous snow" ~ "slight_snow",
    pw == "ice pellets" ~ "ice_pellets",
    pw == "moderate/heavy snow pellet shower" ~ "snow_pellets",
    pw == "ice crystals" ~ "ice_crystals",
    pw == "fog, sky obscured" ~ "dense_fog",
    pw %in% c("fog, sky visible", "fog patches",
               "shallow fog continuous",
               "shallow fog patches") ~ "fog",
    pw == "mist" ~ "mist",
    pw %in% c("severe duststorm, increasing",
               "severe duststorm, decreasing",
               "severe duststorm, steady") ~ "severe_dust",
    str_detect(coalesce(pw, ""), "duststorm") ~ "slight_dust",
    pw == "haze" ~ "haze",
    pw == "smoke reducing visibility" ~ "smoke",
    pw == "squalls" ~ "squalls",
    pw == "lightning, no thunder" ~ "lightning",
    TRUE ~ NA_character_
  )
}

# WMO ww numeric code to bucket, for MW2/MW3 which aren't translated to text
wmo_to_bucket <- function(code) {
  case_when(
    code %in% c("64", "65") ~ "heavy_rain",
    code %in% c("62", "63") ~ "moderate_rain",
    code %in% c("60", "61") ~ "slight_rain",
    code %in% c("80", "81", "82") ~ "slight_rain",
    code %in% c("54", "55") ~ "heavy_drizzle",
    code %in% c("50", "51", "52", "53") ~ "slight_drizzle",
    code %in% c("74", "75") ~ "heavy_snow",
    code %in% c("72", "73") ~ "moderate_snow",
    code %in% c("70", "71") ~ "slight_snow",
    code %in% c("85", "86") ~ "slight_snow",
    code == "79" ~ "ice_pellets",
    code == "77" ~ "snow_pellets",
    code == "76" ~ "ice_crystals",
    code %in% c("66", "67") ~ "freezing_rain",
    code %in% c("68", "69") ~ "mixed_rain_snow",
    code %in% c("83", "84") ~ "mixed_rain_snow",
    code %in% c("87", "88", "89", "90") ~ "hail",
    code == "27" ~ "hail",
    code %in% c("91", "92", "93", "94", "95", "96", "99") ~ "thunderstorm_moderate_rain",
    code == "97" ~ "thunderstorm_heavy",
    code %in% c("17", "98") ~ "thunderstorm_only",
    code %in% c("45", "46", "47", "48", "49") ~ "dense_fog",
    code %in% c("40", "41", "42", "43", "44") ~ "fog",
    code %in% c("11", "12") ~ "fog",
    code == "10" ~ "mist",
    code %in% c("30", "31", "32") ~ "slight_dust",
    code %in% c("33", "34", "35") ~ "severe_dust",
    code == "05" ~ "haze",
    code == "04" ~ "smoke",
    code == "18" ~ "squalls",
    code == "13" ~ "lightning",
    TRUE ~ NA_character_
  )
}

# ASOS code to bucket, for AW5 (not translated to text above).
# 90s range differs from WMO: AW 90/95 = TS no precip, 91 = TS slight rain, 92 = TS mod/heavy rain
aw_to_bucket <- function(code) {
  case_when(
    code %in% c("64", "65") ~ "heavy_rain",
    code %in% c("62", "63") ~ "moderate_rain",
    code %in% c("60", "61") ~ "slight_rain",
    code %in% c("54", "55", "52") ~ "heavy_drizzle",
    code %in% c("50", "51") ~ "slight_drizzle",
    code %in% c("74", "75") ~ "heavy_snow",
    code %in% c("72", "73") ~ "moderate_snow",
    code %in% c("70", "71") ~ "slight_snow",
    code == "79" ~ "ice_pellets",
    code %in% c("66", "67") ~ "freezing_rain",
    code == "90" ~ "thunderstorm_only",
    code == "91" ~ "thunderstorm_slight_rain",
    code == "92" ~ "thunderstorm_moderate_rain",
    code == "95" ~ "thunderstorm_only",
    code == "96" ~ "thunderstorm_hail",
    code == "97" ~ "thunderstorm_heavy",
    code %in% c("41", "42", "43", "44") ~ "fog",
    code %in% c("45", "46") ~ "dense_fog",
    code %in% c("11", "12") ~ "fog",
    code == "10" ~ "mist",
    code %in% c("30", "31") ~ "slight_dust",
    code == "35" ~ "severe_dust",
    code == "05" ~ "haze",
    code == "04" ~ "smoke",
    code == "18" ~ "squalls",
    code == "27" ~ "hail",
    TRUE ~ NA_character_
  )
}

# helper used in the mist/fog correction below. if a secondary slot has actual precip,
# return that precip bucket (stripping any thunderstorm wrapper: e.g. thunderstorm_slight_rain
# becomes slight_rain so the precip takes over as primary). if the slot has something
# non-precip like fog or haze, return NA so it doesn't trigger the correction.
mist_qualifying <- function(cls) {
  case_when(
    cls %in% c("heavy_rain", "moderate_rain", "slight_rain",
                "heavy_drizzle", "slight_drizzle",
                "heavy_snow", "moderate_snow", "slight_snow",
                "freezing_rain", "mixed_rain_snow",
                "ice_pellets", "snow_pellets", "ice_crystals", "hail") ~ cls,
    cls == "thunderstorm_slight_rain" ~ "slight_rain",
    cls %in% c("thunderstorm_moderate_rain",
                "thunderstorm_heavy") ~ "moderate_rain",
    cls == "thunderstorm_hail" ~ "hail",
    TRUE ~ NA_character_
  )
}

# raw codes from MW2/MW3/AW5 for the slots not covered by text translation

weather <- weather %>% mutate(
  mw2_code = na_if(str_split_fixed(MW2, ",", 2)[, 1], ""),
  mw3_code = na_if(str_split_fixed(MW3, ",", 2)[, 1], ""),
  aw5_code = na_if(str_split_fixed(AW5, ",", 2)[, 1], "")
)

# classify each slot. slots 2/3 fall back to MW2/MW3 numeric codes when text columns are NA

weather <- weather %>% mutate(
  wx_class_1 = classify_wx(present_weather),
  wx_class_2 = coalesce(classify_wx(present_weather_2), wmo_to_bucket(mw2_code)),
  wx_class_3 = coalesce(classify_wx(present_weather_3), wmo_to_bucket(mw3_code)),
  wx_class_4 = classify_wx(present_weather_4),
  wx_class_5 = aw_to_bucket(aw5_code)
)

# when correcting a mist row and slots 2/3 are empty, AU1's precip type is the fallback.
# assigned as slight intensity since these are inherently light events
# (heavy rain would have shown up in slot 2).

weather <- weather %>% mutate(
  au_type_corrected = case_when(
    precip_type == "rain" ~ "slight_rain",
    precip_type == "drizzle" ~ "slight_drizzle",
    precip_type == "snow/sleet" ~ "slight_snow",
    precip_type == "ice pellets" ~ "ice_pellets",
    precip_type == "freezing rain/drizzle" ~ "freezing_rain",
    precip_type == "snow pellets" ~ "snow_pellets",
    TRUE ~ NA_character_
  )
)

# the mist correction: when slot 1 = mist, check slot 2 for actual precip. if found,
# precip becomes primary and mist becomes secondary (or disappears). then slot 3, then AU1.
# only overrides when the secondary slot has precipitation. fog, dust, or TS-only there
# means genuine mist alongside something real, so no correction.

weather <- weather %>% mutate(
  mist_override = case_when(
    coalesce(wx_class_1, "") != "mist" ~ NA_character_,
    !is.na(mist_qualifying(wx_class_2)) ~ mist_qualifying(wx_class_2),
    !is.na(mist_qualifying(wx_class_3)) ~ mist_qualifying(wx_class_3),
    !is.na(au_type_corrected) ~ au_type_corrected,
    TRUE ~ NA_character_
  )
)

# same logic for fog: fog in slot 1 with precip in slot 2 means fog is the visibility modifier

weather <- weather %>% mutate(
  fog_override = case_when(
    !wx_class_1 %in% c("fog", "dense_fog") ~ NA_character_,
    !is.na(mist_qualifying(wx_class_2)) ~ mist_qualifying(wx_class_2),
    TRUE ~ NA_character_
  )
)

weather <- weather %>% mutate(
  wx_primary = case_when(
    !is.na(mist_override) ~ mist_override,
    !is.na(fog_override) ~ fog_override,
    is.na(wx_class_1) & !is.na(precip_mm) & precip_mm > 0 ~ "precip_unclassified",
    TRUE ~ wx_class_1
  )
)

# thunderstorm can show up in any of the 5 slots, so all are checked.
# the AU2 ice pellets check catches cases where the WMO slot called it snow but the ASOS
# sensor correctly identified ice pellets mixed in.

weather <- weather %>% mutate(
  wx_secondary = case_when(
    !str_detect(coalesce(wx_primary, ""), "thunderstorm") & (
      str_detect(coalesce(wx_class_2, ""), "thunderstorm") |
      str_detect(coalesce(wx_class_3, ""), "thunderstorm") |
      str_detect(coalesce(wx_class_4, ""), "thunderstorm") |
      str_detect(coalesce(wx_class_5, ""), "thunderstorm")
    ) ~ "thunderstorm",
    !is.na(mist_override) ~ "mist",
    !is.na(fog_override) ~ "fog",
    !is.na(wx_primary) & wx_class_2 == "fog" ~ "fog",
    wx_class_2 == "freezing_rain" & !is.na(wx_primary) ~ "freezing",
    wx_class_3 == "mixed_rain_snow" & !is.na(wx_primary) ~ "mixed_rain_snow",
    !is.na(wx_primary) & wx_primary != "ice_pellets" &
      coalesce(precip_type_2, "") == "ice pellets" ~ "ice_pellets",
    TRUE ~ NA_character_
  )
)

weather <- weather %>%
  select(-wx_class_1, -wx_class_2, -wx_class_3, -wx_class_4, -wx_class_5,
         -mist_override, -fog_override, -au_type_corrected,
         -mw2_code, -mw3_code, -aw5_code)


write_parquet(weather, "phl_weather_ks.parquet")


#### build final_weather.parquet ####

# stripped-down version for modeling: just the 22 columns that are actually useful.
# all the raw source strings, intermediate weather columns, and redundant fields are gone.
# NA notes: NA in gust_ms means no gust happened (not missing data).
# NA in wx_primary/wx_secondary means no significant weather reported. NA in pressure
# tendency means the 3-hour tendency just wasn't recorded for that observation.

final_weather <- weather %>%
  select(
    DATE,
    temp_c, dewpt_c,
    wind_dir, wind_speed_ms, gust_ms,
    slp_hpa, pressure_tendency, pressure_3hr_change_hpa,
    ceiling_m, vis_m,
    cloud_cover_1, cloud_base_m_1,
    precip_mm, precip_trace,
    ice_accretion_cm,
    wx_primary, wx_secondary,
    daily_snow_depth_cm, daily_snow_24h_mm,
    daily_peak1min_ms, daily_mean_wind_ms
  )

# DATE is stored as "2021-03-15T14:54:00" (ISO 8601, always UTC).
# converting to a proper datetime for extracting hour, month, etc. downstream.
# note: UTC is 5 hours ahead of eastern standard, 4 ahead of eastern daylight.
# for local time instead: mutate(DATE = with_tz(DATE, "America/New_York"))

final_weather <- final_weather %>%
  mutate(DATE = ymd_hms(DATE, tz = "UTC")) %>%
  # KATIE: EDITED
  # Added conversion to Eastern to streamline merge to OTP
  mutate(DATE = with_tz(DATE, "America/New_York"))

# wx_primary and wx_secondary abbreviated and converted to factors.
# abbreviation key:
#   HR=heavy_rain, MR=moderate_rain, SR=slight_rain
#   HDZ=heavy_drizzle, DZ=slight_drizzle
#   HS=heavy_snow, MS=moderate_snow, SS=slight_snow
#   FR=freezing_rain, RS=mixed_rain_snow, IP=ice_pellets, SP=snow_pellets, IC=ice_crystals, HL=hail
#   TS=thunderstorm_only, TSLR=thunderstorm_slight_rain, TSMR=thunderstorm_moderate_rain
#   TSHY=thunderstorm_heavy, TSHL=thunderstorm_hail
#   FG=fog, DFG=dense_fog, MI=mist, HZ=haze, SM=smoke
#   SQ=squalls, LTG=lightning, DU=slight_dust, SDU=severe_dust, PU=precip_unclassified

wx_primary_abbr <- c(
  heavy_rain = "HR", moderate_rain = "MR", slight_rain = "SR",
  heavy_drizzle = "HDZ", slight_drizzle = "DZ",
  heavy_snow = "HS", moderate_snow = "MS", slight_snow = "SS",
  freezing_rain = "FR", mixed_rain_snow = "RS",
  ice_pellets = "IP", snow_pellets = "SP", ice_crystals = "IC", hail = "HL",
  thunderstorm_only = "TS", thunderstorm_slight_rain = "TSLR",
  thunderstorm_moderate_rain = "TSMR", thunderstorm_heavy = "TSHY",
  thunderstorm_hail = "TSHL",
  fog = "FG", dense_fog = "DFG", mist = "MI",
  haze = "HZ", smoke = "SM", squalls = "SQ", lightning = "LTG",
  slight_dust = "DU", severe_dust = "SDU",
  precip_unclassified = "PU"
)

wx_secondary_abbr <- c(
  thunderstorm = "TS", mist = "MI", fog = "FG",
  freezing = "FZ", mixed_rain_snow = "RS", ice_pellets = "IP"
)

final_weather <- final_weather %>%
  mutate(
    wx_primary = factor(wx_primary_abbr[wx_primary], levels = wx_primary_abbr),
    wx_secondary = factor(wx_secondary_abbr[wx_secondary], levels = wx_secondary_abbr)
  )

write_parquet(final_weather, "final_weather_ks.parquet")
