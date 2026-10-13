"""Static MRF-discovery data tables — extracted verbatim from build_data.py.

DOMAIN_HINTS: name-substring -> price-transparency domain, longest-pattern-first.
KNOWN_MRF: CCN -> explicit MRF URL(s) for hospitals whose files can't be found
by domain guessing. Both are pure data (no runtime state, no function calls);
build_data.py imports them by name. See the care-cost-explorer skill for the
dedupe/verification rules that govern edits to these tables.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# HOSPITAL WEBSITE RESOLUTION
# The CMS registry has no website field, so we can't discover /cms-hpt.txt
# without knowing each hospital's domain. Two-tier approach:
#   DOMAIN_HINTS  maps hospital-name patterns to the system's domain, which is
#                 enough for cms-hpt.txt discovery to take over.
#   KNOWN_MRF     hardcodes file URLs already confirmed by hand, for systems
#                 where automatic discovery fails.
# Both are keyed by lowercase substring match on the CMS facility name.
# Add entries here as you expand to new regions.
# ---------------------------------------------------------------------------
DOMAIN_HINTS = [
    # --- San Diego (verified) ---
    ("scripps",              "scripps.org"),
    ("sharp",                "sharp.com"),
    ("grossmont",            "sharp.com"),
    ("uc san diego",         "health.ucsd.edu"),
    ("ucsd",                 "health.ucsd.edu"),
    ("jacobs medical",       "health.ucsd.edu"),
    ("east campus",          "health.ucsd.edu"),
    ("rady",                 "rchsd.org"),
    ("palomar",              "palomarhealth.org"),
    ("pomerado",             "palomarhealth.org"),
    ("tri-city",             "tricitymed.org"),
    ("tri city",             "tricitymed.org"),
    ("alvarado",             "alvaradohospital.com"),
    ("paradise valley",      "pvhospital.org"),

    # --- large systems, which account for most hospitals ---
    # A wrong guess here costs one failed lookup; it cannot produce wrong
    # data, because every discovered file is verified to name the hospital
    # before it's used.
    ("kaiser",               "healthy.kaiserpermanente.org"),
    ("sutter",               "sutterhealth.org"),
    # Sutter sub-brands publish nothing on their own domains; the parent's
    # UTF-16 manifest carries all of them (CalPac, Alta Bates, Novato, Eden,
    # Los Banos, Memorial Medical Center...).
    ("california pacific",   "sutterhealth.org"),
    ("alta bates",           "sutterhealth.org"),
    ("novato community",     "sutterhealth.org"),
    ("eden medical",         "sutterhealth.org"),
    ("dignity",              "dignityhealth.org"),
    ("commonspirit",         "commonspirit.org"),
    ("providence",           "providence.org"),
    # Providence (RWJ) CA members under other names — all in the 61-location
    # providence.org manifest.
    ("petaluma valley",      "providence.org"),
    ("healdsburg hospital",  "providence.org"),
    ("queen of the valley",  "providence.org"),
    ("saint mary medical",   "providence.org"),
    ("adventist",            "adventisthealth.org"),
    ("emanate health",       "emanatehealth.org"),
    ("prime healthcare",     "primehealthcare.com"),
    ("tenet",                "tenethealth.com"),
    ("hca ",                 "hcahealthcare.com"),
    ("memorialcare",         "memorialcare.org"),
    ("memorial care",        "memorialcare.org"),
    ("cedars",               "cedars-sinai.org"),
    ("stanford",             "stanfordhealthcare.org"),
    ("ucla",                 "uclahealth.org"),
    ("ronald reagan",        "uclahealth.org"),
    ("santa monica ucla",    "uclahealth.org"),
    ("ucsf",                 "ucsfhealth.org"),
    ("uc irvine",            "ucihealth.org"),
    ("uci medical",          "ucihealth.org"),
    ("uc davis",             "health.ucdavis.edu"),
    ("keck",                 "keckmedicine.org"),
    ("usc ",                 "keckmedicine.org"),
    ("loma linda",           "lluh.org"),
    ("hoag",                 "hoag.org"),
    ("cottage",              "cottagehealth.org"),
    ("john muir",            "johnmuirhealth.com"),
    ("el camino",            "elcaminohealth.org"),
    ("valley children",      "valleychildrens.org"),
    ("children's hospital los angeles", "chla.org"),
    ("city of hope",         "cityofhope.org"),
    ("scripps mercy",        "scripps.org"),
    ("salinas valley",       "salinasvalleyhealth.com"),
    ("torrance memorial",    "torrancememorial.org"),
    ("huntington",           "huntingtonhealth.org"),
    ("pomona valley",        "pvhmc.org"),
    ("eisenhower",           "eisenhowerhealth.org"),
    ("desert regional",      "desertcarenetwork.com"),
    ("enloe",                "enloe.org"),
    ("marshall",             "marshallmedical.org"),
    ("washington hospital",  "whhs.com"),
    ("stanford children",    "stanfordchildrens.org"),
    ("lucile packard",         "stanfordchildrens.org"),

    # --- Florida systems ---
    ("adventhealth",           "adventhealth.com"),        # AdventHealth (21 FL hospitals incl. Orlando, Winter Haven, Parrish, Flagler, Lakeland)
    ("baptist health",         "baptisthealth.net"),       # Baptist Health South Florida (Broward/Miami-Dade/Palm Beach)
    ("baptist hospital of miami", "baptisthealth.net"),
    ("west kendale baptist",     "baptisthealth.net"),
    ("south florida baptist",    "baptisthealth.net"),
    ("doctors memorial hospital", "baptisthealth.net"),
    ("homestead hospital",       "baptisthealth.net"),
    ("larkin community hospital","larkinhospital.com"),    # independent hospital, not Baptist Health
    ("coral gables hospital",    "baptisthealth.net"),
    ("wellington regional medical center", "baptisthealth.net"),
    ("palm beach gardens medical center", "baptisthealth.net"),
    ("boca raton regional hospital","baptisthealth.net"),
    ("bethesda hospital east",   "baptisthealth.net"),
    ("viera hospital",           "hcahealthcare.com"),     # HCA Florida (was Baptist, now HCA)
    ("baptist medical center - nassau", "baptistjax.com"),  # Baptist Health North Florida — domain dead? verify
    ("baptist health medical center - jacksonville", "baptistjax.com"),  # Baptist Health North Florida
    ("shands jacksonville",      "baptistjax.com"),# Baptist Health North Florida (owns Shands Jax)
    ("hca florida",            "hcahealthcare.com"),       # HCA Florida hospitals (Palms West, West, Holy Cross, Hialeah, etc.)
    ("orlando health",         "orlandohealth.com"),
    ("tampa general hospital", "tgh.org"),                 # Tampa General Hospital — corrected; both earlier guesses (tgch.com/tgmu.edu) were wrong
    ("mayo clinic",            "mayoclinic.org"),          # Mayo Clinic Florida (Jacksonville)
    ("johns hopkins all children", "allchildrenshospital.com"),  # ambiguous — unconfirmed vs. jhch.org
    ("nicklaus",               "nicklauschildrens.org"),   # Nicklaus Children's (Miami)
    ("nemours",                "nemours.org"),             # Nemours (Aventura)
    ("cleveland clinic",       "clevelandclinic.org"),     # Cleveland Clinic FL hospitals (mychart subdomain is patient portal, not price files)
    ("halifax health",         "halifaxhealth.com"),       # Halifax Health (Titusville/DeLand) — TLS cert mismatch? verify

    ("broward health",         "browardhealth.org"),       # Broward Health (Miramar, Coral Springs, Plantation, etc.)
    ("lakeside medical center","bayfronthealth.com"),          # Lakeside Medical Center (St. Pete) — domain dead? verify
    ("ascension st vincent",   "ascension.org"),           # Ascension St. Vincent (Tampa) — consolidated under ascension.org
    ("ascension sacred heart", "ascension.org"),           # Ascension Sacred Heart (Tampa/Daytona) — consolidated under ascension.org
    ("sarasota memorial hospital", "smh.com"),             # Sarasota Memorial Health Care System
    ("jupiter medical center", "baptisthealth.net"),       # Jupiter Medical Center = Baptist Health South Florida
    ("westside regional medical center", "hcahealthcare.com"),  # Westside Regional (Plantation) = HCA Florida — corrected; both earlier guesses were wrong
    ("memorial healthcare system", "memorialhcs.org"),   # Memorial Healthcare System (Miramar, Broward) — confirmed alive
    ("north shore medical center", "northshoremc.com"),   # North Shore Medical Center (Pompano) — confirmed alive, correct title
    ("memorial hospital miramar", "memorialhcs.org"),      # Memorial Healthcare System
    ("memorial regional hospital", "memorialhcs.org"),     # Memorial Healthcare System
    ("memorial hospital west",   "memorialhcs.org"),       # Memorial Healthcare System (West)
    ("morton plant hospital",  "baptisthealth.net"),       # Morton Plant (Clearwater) = Baptist Health SF? verify — may be independent
    ("morton plant",           "baptisthealth.net"),
    ("south miami hospital",   "baptisthealth.net"),       # South Miami Hospital = Baptist Health SF? verify
    ("jackson health system",  "jacksonhealthsystem.org"), # Jackson Health System (Miami-Dade County) — domain dead? verify
    ("st lucie medical center","stluciehealth.com"),       # St. Lucie Medical Center (Port St. Lucie) — confirmed alive
    ("good samaritan medical center", "palmbeachhealthnetwork.com"),  # Good Samaritan (West Palm Beach) = Palm Beach Health Network (Tenet)
    ("west boca medical center", "palmbeachhealthnetwork.com"),       # West Boca (Boca Raton) = PBHN (Tenet)
    ("st mary's medical center", "palmbeachhealthnetwork.com"),       # St. Mary's (West Palm Beach) = PBHN (Tenet)
    ("delray medical center",  "palmbeachhealthnetwork.com"),         # Delray Medical Center = PBHN (Tenet)
    ("florida coast medical center", "palmbeachhealthnetwork.com"),   # Florida Coast MC (Port St. Lucie) = PBHN (Tenet)
    ("lakeland regional medical center", "adventhealth.com"),  # Lakeland Regional = AdventHealth
    ("winter haven hospital",  "adventhealth.com"),         # AdventHealth Winter Haven
    ("parrish medical center", "adventhealth.com"),         # AdventHealth Parrish (Wesley Chapel)
    ("flagler hospital",       "adventhealth.com"),         # AdventHealth Flagler (Palm Coast)
    ("baycare hospital wesley chapel", "baycare.org"),      # BayCare (independent, Wesley Chapel/Tampa) — verify domain
    ("st anthony's hospital",  "baycare.org"),   # St. Anthony's (St. Pete/Palm Beach County) — verify

    ("manatee memorial hospital", "adventhealth.com"),      # Manatee Memorial (Bradenton) = AdventHealth? verify
    ("holy cross hospital",   "hcahealthcare.com"),         # HCA Florida Holy Cross (Pompano)
    ("gulf breeze hospital",  "gulfbreeze.org"),            # Gulf Breeze Hospital (Pensacola) — verify

    ("desoto memorial hospital", "adventhealth.com"),       # DeSoto Memorial (Bartow) = AdventHealth? verify
    ("ed fraser memorial hospital", "baptisthealth.net"),   # Ed Fraser Memorial (Crestview) = Baptist Health North FL? verify
    ("north okaloosa medical center", "hcahealthcare.com"), # HCA Florida North Okaloosa (Fort Walton Beach) — verify
    ("north walton doctors hospital", "baptisthealth.net"), # North Walton Doctors (DeFuniak Springs) = Baptist Health North FL? verify
    ("broward health coral springs", "browardhealth.org"),
    ("broward health imperial point", "browardhealth.org"),
    ("broward health medical center", "browardhealth.org"),
    ("broward health north",   "browardhealth.org"),
    ("mease countryside hospital", "baycare.org"),            # Mease (Clearwater) — verify domain
    ("mease dunedin hospital", "baycare.org"),                # Mease (Dunedin)
    ("mease ",                 "baycarehealth.org"),
    ("uf health shands hospital", "ufhealth.org"),          # UF Health Shands (Gainesville) — corrected; shands.org is legacy
    ("uf health leesburg hospital", "ufhealth.org"),        # UF Health Leesburg
    ("ucf lake nona hospital", "orlandohealth.com"),        # UCF Lake Nona = Orlando Health? verify
    ("lee health",             "leehospital.org"),
    ("palm beach west",        "baptisthealth.net"),

    # --- federal facilities: exempt, don't waste lookups ---
    ("naval medical",        None),
    ("veterans affairs",     None),
    ("va medical",           None),
    ("nh ",                  None),
    ("nmc ",                 None),
]


# Verified by hand from each system's own price transparency page.
# Keys are matched against a punctuation-stripped facility name, so
# "Scripps Memorial Hospital - Encinitas" matches "scripps memorial hospital encinitas".
KNOWN_MRF = {
    "scripps green hospital":
        "https://apps.scripps.org/pricetransparency/951684089_Scripps-Green-Hospital_standardcharges.csv",
    "scripps memorial hospital encinitas":
        "https://apps.scripps.org/pricetransparency/951684089_Scripps-Memorial-Hospital-Encinitas_standardcharges.csv",
    "scripps memorial hospital la jolla":
        "https://apps.scripps.org/pricetransparency/951684089_Scripps-Memorial-Hospital-La-Jolla_standardcharges.csv",
    "scripps mercy hospital chula vista":
        "https://apps.scripps.org/pricetransparency/951684089_Scripps-Mercy-Hospital-Chula-Vista_standardcharges.csv",
    "scripps mercy hospital san diego":
        "https://apps.scripps.org/pricetransparency/951684089_Scripps-Mercy-Hospital-San-Diego_standardcharges.csv",
    # CMS lists the Hillcrest campus simply as "Scripps Mercy Hospital"
    "scripps mercy hospital":
        "https://apps.scripps.org/pricetransparency/951684089_Scripps-Mercy-Hospital-San-Diego_standardcharges.csv",
    # HCA Florida: publishes on Azure blob (stctrprodsnsvc00455826e6). The
    # signed SAS URLs in these manifests rotate, so we store only the base
    # blob path and resolve a live link from hcafloridahealthcare.com/cms-hpt.txt
    # at run time (refresh_sas_url). If HCA rotates to a new storage account,
    # the manifest lookup follows them; if it's gone entirely, DISCOVER takes
    # over.
    "hca florida citrus hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/47-1455535_HCA-FLORIDA-CITRUS-HOSPITAL_standardcharges.json",
    "hca florida highlands hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/82-2084329_HCA-FLORIDA-HIGHLANDS-HOSPITAL_standardcharges.json",
    "hca florida twin cities hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/59-1836808_HCA-FLORIDA-TWIN-CITIES-HOSPITAL_standardcharges.json",
    "hca florida jfk hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/62-1694180_HCA-FLORIDA-JFK-HOSPITAL_standardcharges.json",
    "hca florida lehigh hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/87-1999484_HCA-FLORIDA-LEHIGH-HOSPITAL_standardcharges.json",
    "hca florida osceola hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/61-1257509_HCA-FLORIDA-OSCEOLA-HOSPITAL_standardcharges.json",
    "hca florida pasadena hospital a part of":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/80-0935610_HCA-FLORIDA-PASADENA-HOSPITAL_standardcharges.json",
    # Remaining HCA Florida hospitals, sourced from the network's own
    # cms-hpt.txt manifest at hcafloridahealthcare.com/cms-hpt.txt
    "hca florida aventura hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/75-2379007_HCA-FLORIDA-AVENTURA-HOSPITAL_standardcharges.json",
    "hca florida lake city hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/95-4176924_HCA-FLORIDA-LAKE-CITY-HOSPITAL_standardcharges.json",
    "hca florida sarasota doctors hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/61-1258724_HCA-FLORIDA-SARASOTA-DOCTORS-HOSPITAL_standardcharges.json",
    "hca florida mercy hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/62-1372389_HCA-FLORIDA-MERCY-HOSPITAL_standardcharges.json",
    "hca florida memorial hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/59-3283127_HCA-FLORIDA-MEMORIAL-HOSPITAL_standardcharges.json",
    "hca florida st petersburg hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/59-1174716_HCA-FLORIDA-ST.-PETERSBURG-HOSPITAL_standardcharges.json",
    "hca florida northwest hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/61-1259843_HCA-FLORIDA-NORTHWEST-HOSPITAL_standardcharges.json",
    "hca florida trinity hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/59-2047041_HCA-FLORIDA-TRINITY-HOSPITAL_standardcharges.json",
    "hca florida north florida hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/61-1269294_HCA-FLORIDA-NORTH-FLORIDA-HOSPITAL_standardcharges.json",
    "hca florida south tampa hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/36-4764806_HCA-FLORIDA-SOUTH-TAMPA-HOSPITAL_standardcharges.json",
    "hca florida kendall hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/65-0260078_HCA-FLORIDA-KENDALL-HOSPITAL_standardcharges.json",
    "hca florida blake hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/62-1113740_HCA-FLORIDA-BLAKE-HOSPITAL_standardcharges.json",
    "hca florida fort walton destin hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/61-1259833_HCA-FLORIDA-FORT-WALTON-DESTIN-HOSPITAL_standardcharges.json",
    "hca florida orange park hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/61-1269295_HCA-FLORIDA-ORANGE-PARK-HOSPITAL_standardcharges.json",
    "hca florida west hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/59-1525468_HCA-FLORIDA-WEST-HOSPITAL_standardcharges.json",
    "hca florida putnam hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/47-2762362_HCA-FLORIDA-PUTNAM-HOSPITAL_standardcharges.json",
    "hca florida fawcett hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/65-0252846_HCA-FLORIDA-FAWCETT-HOSPITAL_standardcharges.json",
    "hca florida northside hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/61-0947837_HCA-FLORIDA-NORTHSIDE-HOSPITAL_standardcharges.json",
    "hca florida gulf coast hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/62-0976863_HCA-FLORIDA-GULF-COAST-HOSPITAL_standardcharges.json",
    "hca florida brandon hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/61-0947837_HCA-FLORIDA-BRANDON-HOSPITAL_standardcharges.json",
    "hca florida lawnwood hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/59-1764486_HCA-FLORIDA-LAWNWOOD-HOSPITAL_standardcharges.json",
    "hca florida largo hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/62-1026428_HCA-FLORIDA-LARGO-HOSPITAL_standardcharges.json",
    "hca florida raulerson hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/59-1833934_HCA-FLORIDA-RAULERSON-HOSPITAL_standardcharges.json",
    "hca florida capital hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/62-1091430_HCA-FLORIDA-CAPITAL-HOSPITAL_standardcharges.json",
    "hca florida bayonet point hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/62-1113740_HCA-FLORIDA-BAYONET-POINT-HOSPITAL_standardcharges.json",
    "hca florida south shore hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/59-2822337_HCA-FLORIDA-SOUTH-SHORE-HOSPITAL_standardcharges.json",
    "hca florida oak hill hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/62-1113740_HCA-FLORIDA-OAK-HILL-HOSPITAL_standardcharges.json",
    "hca florida englewood hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/35-1611050_HCA-FLORIDA-ENGLEWOOD-HOSPITAL_standardcharges.json",
    # AdventHealth Florida: adventhealth.com/cms-hpt.txt lists each facility's
    # HospitalPriceDisclosure.com redirector (download.aspx); resolved
    # each one here to the direct blob it 302s to, so no extra hop at fetch time.
    "adventhealth orlando":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/590724459_adventhealth-orlando_standardcharges.json",
    "adventhealth new smyrna beach":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/473793197_adventhealth-new-smyrna-beach_standardcharges.json",
    "adventhealth deland":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/593256803_adventhealth-deland_standardcharges.json",
    "adventhealth zephyrhills":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/592108057_adventhealth-zephyrhills_standardcharges.json",
    "adventhealth north pinellas":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/590898901_adventhealth-north-pinellas_standardcharges.json",
    "adventhealth waterman":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/593140669_adventhealth-waterman_standardcharges.json",
    "adventhealth ocala":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/824372339_adventhealth-ocala_standardcharges.json",
    "adventhealth daytona beach":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/590973502_adventhealth-daytona-beach_standardcharges.json",
    "adventhealth carrollwood":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/591113901_adventhealth-carrollwood_standardcharges.json",
    "adventhealth fish memorial":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/593149293_adventhealth-fish-memorial_standardcharges.json",
    "adventhealth port charlotte":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/332257010_adventhealth-port-charlotte_standardcharges.json",
    "adventhealth lake wales":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/834672945_adventhealth-lake-wales_standardcharges.json",
    "adventhealth sebring":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/590725553_adventhealth-sebring_standardcharges.json",
    "adventhealth heart of florida":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/841793121_adventhealth-heart-of-florida_standardcharges.json",
    "adventhealth tampa":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/591113901_adventhealth-tampa_standardcharges.json",
    "adventhealth dade city":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/822567308_adventhealth-dade-city_standardcharges.json",
    "adventhealth wesley chapel":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/208488713_adventhealth-wesley-chapel_standardcharges.json",
    "adventhealth riverview":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/870901094_adventhealth-riverview_standardcharges.json",
    "adventhealth wauchula":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/590725553_adventhealth-wauchula_standardcharges.json",
    # Ascension St. Vincent's (Florida): hrefs harvested from
    # healthcare.ascension.org/price-transparency/price-transparency-files
    # (Florida section); domain must be healthcare.ascension.org, not ascension.org.
    # The site 308-redirects the .csv link to a .zip of the same name, so the
    # zip URL is used directly here (stream_zip_rows handles the extension).
    "ascension st vincent's riverside":
        "https://healthcare.ascension.org/-/media/project/ascension/healthcare/price-transparency-files/fl-csv/590624449_st-vincents-medical-center-inc_standardcharges.zip",
    "ascension st vincent's southside":
        "https://healthcare.ascension.org/-/media/project/ascension/healthcare/price-transparency-files/fl-csv/260479484_st-lukes-st-vincents-healthcare-inc_standardcharges.zip",
    "ascension st vincent's clay county":
        "https://healthcare.ascension.org/-/media/project/ascension/healthcare/price-transparency-files/fl-csv/461523194_united-states-catholic-conference-st-vincents-medical-center-clay-county-inc_standardcharges.zip",
    "ascension st vincent's st johns county":
        "https://healthcare.ascension.org/-/media/project/ascension/healthcare/price-transparency-files/fl-csv/593650609_st-vincents-health-system-inc_standardcharges.zip",
    # Broward Health: browardhealth.org/cms-hpt.txt lists these blob URLs; the
    # live domain sits behind a Cloudflare JS challenge that blocks automated
    # fetches of the manifest itself, so it was read via a Wayback Machine
    # snapshot, but the blob URLs below were verified live and directly.
    "broward health medical center":
        "https://sthpiprd.blob.core.windows.net/machine-readable-files/10969/596012065_broward-health-medical-center_standardcharges.csv",
    "broward health north":
        "https://sthpiprd.blob.core.windows.net/machine-readable-files/10970/596012065_broward-health-north_standardcharges.csv",
    "broward health imperial point":
        "https://sthpiprd.blob.core.windows.net/machine-readable-files/10968/596012065_broward-health-imperial-point_standardcharges.csv",
    "broward health coral springs":
        "https://sthpiprd.blob.core.windows.net/machine-readable-files/10967/596012065_broward-health-coral-springs_standardcharges.csv",
    # Cleveland Clinic Florida: MRF download redirectors linked from
    # my.clevelandclinic.org/patients/billing-finance/comprehensive-hospital-charges
    "cleveland clinic martin north hospital":
        "https://clevelandclinic.pt.panaceainc.com/MRFDownload/clevelandclinic/martin-north-south-tradition",
    "cleveland clinic indian river hospital":
        "https://clevelandclinic.pt.panaceainc.com/MRFDownload/clevelandclinic/indianriver",
    "cleveland clinic hospital":
        "https://clevelandclinic.pt.panaceainc.com/MRFDownload/clevelandclinic/weston",
    # Orlando Health: hospitalpricedisclosure.com redirectors linked from each
    # facility's location-name entry in orlandohealth.com/cms-hpt.txt; resolved
    # to the direct blob each one 302s to. CMS lists the flagship campus simply
    # as "Orlando Health", which the fallback pattern below covers.
    "orlando health health central hospital":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/800764192_orlando-health-central,-inc._standardcharges.json",
    "orlando health bayfront hospital":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/845074208_ohi-west,-inc._standardcharges.json",
    "orlando health south lake hospital":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/593322533_south-lake-hospital,-inc._standardcharges.json",
    "orlando health sebastian river hospital":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/99-4762475_orlando-health-sebastian-river-hospital_standardcharges.json",
    "orlando health melbourne hospital":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/99-4761958_orlando-health-melbourne-hospital_standardcharges.json",
    "orlando health st cloud hospital":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/203728235_1164478442_orlando-health,-inc._standardcharges.json",
    "orlando health":
        "https://cleverleypteusstatic.blob.core.windows.net/readable/591726273_orlando-health,-inc._standardcharges.json",
    # Lee Health (Fort Myers/Cape Coral): leehealth.org/cms-hpt.txt
    "lee memorial hospital":
        "https://www.leehealth.org/machine-readable-files-lh-2026/99-2646504_LEE-MEMORIAL-HOSPITAL_standardcharges_20260401.zip",
    "cape coral hospital":
        "https://www.leehealth.org/machine-readable-files-lh-2026/99-2646504_CAPE-CORAL-HOSPITAL_standardcharges_20260401.zip",
    "gulf coast medical center":
        "https://www.leehealth.org/machine-readable-files-lh-2026/99-2646504_GULF-COAST-MEDICAL-CENTER_standardcharges_20260401.zip",
    # Health First (Brevard County): hf.org/cms-hpt.txt
    "holmes regional medical center":
        "https://www.hf.org/sites/default/files/2026-03/590624371_Holmes-Regional-Medical-Center_standardcharges.csv",
    "cape canaveral hospital":
        "https://www.hf.org/sites/default/files/2026-03/592477479_Cape-Canaveral-Hospital_standardcharges.csv",
    "palm bay hospital":
        "https://www.hf.org/sites/default/files/2026-03/590624371_Palm-Bay-Hospital_standardcharges.csv",
    # Memorial Healthcare System (Broward): mhs.net/cms-hpt.txt
    "memorial hospital miramar":
        "https://www.mhs.net/-/media/Files/Standard%20charges/596014973memorialhospitalmiramarstandardchargescsv.zip",
    "memorial hospital west":
        "https://www.mhs.net/-/media/Files/Standard%20charges/596014973memorialhospitalweststandardchargescsv.zip",
    "memorial hospital pembroke":
        "https://www.mhs.net/-/media/Files/Standard%20charges/596014973memorialhospitalpembrokestandardchargescsv.zip",
    # BayCare Health System (Tampa Bay): baycare.org/cms-hpt.txt
    "morton plant hospital":
        "https://baycare.org/-/media/project/baycare/consumer-portal/billing-and-insurance/pricing-files-compressed/590624462_MortonPlantHospital_standardcharges.zip",
    "morton plant north bay hospital":
        "https://baycare.org/-/media/project/baycare/consumer-portal/billing-and-insurance/pricing-files-compressed/590624462_MortonPlantNorthBayHospital_standardcharges.zip",
    "st josephs hospital":
        "https://baycare.org/-/media/project/baycare/consumer-portal/billing-and-insurance/pricing-files-compressed/590774199_StJosephsHospital_standardcharges.zip",
    "st anthonys hospital":
        "https://baycare.org/-/media/project/baycare/consumer-portal/billing-and-insurance/pricing-files-compressed/592043026stanthonyshospitalstandardcharges.zip",
    "bartow regional medical center":
        "https://baycare.org/-/media/project/baycare/consumer-portal/billing-and-insurance/pricing-files-compressed/475387418_bartowregionalmedicalcenter_standardcharges.zip",
    # Baptist Health (Jacksonville, North Florida): baptistjax.com gross-charges
    # page links to panaceainc.com MRF redirectors, same pattern as Cleveland
    # Clinic FL and Halifax Health below.
    "baptist health medical center - jacksonville":
        "https://baptistjacksonville.pt.panaceainc.com/MRFDownload/baptistjacksonville/baptistjacksonville",
    "baptist medical center  beaches":
        "https://baptistjacksonville.pt.panaceainc.com/MRFDownload/baptistjacksonville/baptistbeaches",
    # NOTE: "Shands Jacksonville" is NOT part of Baptist Health — it's UF
    # Health Jacksonville's former name. It publishes on UF Health's own
    # panaceainc.com org, alongside UF Health Shands (Gainesville).
    "shands jacksonville":
        "https://ufhealth.pt.panaceainc.com/MRFDownload/ufhealth/uf-jax",
    # Ed Fraser Memorial Hospital (Macclenny, Baker County): independent,
    # operated by Baker County Medical Services Inc — unrelated to Baptist
    # Medical Center Clay despite both being near Jacksonville.
    "ed fraser memorial hospital":
        "https://hospitalpricetransparencyfiles.com/baker-county-medical-services-inc/593202547_Baker-County-Medical-Services-Inc_standardcharges.csv",
    # Halifax Health (Daytona Beach/DeLand): halifaxhealth.org/cms-hpt.txt
    "halifax health medical center":
        "https://halifax.pt.panaceainc.com/MRFDownload/halifax/halifax-medical-center",
    "halifax health /uf health medical center of delton":
        "https://halifax.pt.panaceainc.com/MRFDownload/halifax/deltona",
    # Ascension Sacred Heart (Pensacola/Panhandle): distinct manifest section from
    # Ascension St. Vincent's (Jacksonville), same fl-csv host
    "sacred heart hospital":
        "https://healthcare.ascension.org/-/media/project/ascension/healthcare/price-transparency-files/fl-csv/590634434_sacred-heart-health-system-inc_standardcharges.csv",
    # NOTE: this facility's URL used to 404 as a .csv; the site 308-redirects
    # it to a .zip of the same name (same pattern as the Ascension St.
    # Vincent's entries above), verified live.
    "sacred heart hospital on the emerald coast":
        "https://healthcare.ascension.org/-/media/project/ascension/healthcare/price-transparency-files/fl-csv/721529708_sacred-heart-health-system-inc_standardcharges.zip",
    "ascension sacred heart gulf":
        "https://healthcare.ascension.org/-/media/project/ascension/healthcare/price-transparency-files/fl-csv/300577249_sacred-heart-health-system-inc_standardcharges.csv",
    "ascension sacred heart bay":
        "https://healthcare.ascension.org/-/media/project/ascension/healthcare/price-transparency-files/fl-csv/900799724_bay-county-health-system-llc_standardcharges.csv",
    # Baptist Health South Florida: baptisthealth.net/cms-hpt.txt
    "doctors hospital":
        "https://baptisthealth.net/-/media/Documents/Patient-Resources/Patient-Pricing/Apr-2026/43775926_doctors-hospital_standardcharges.zip",
    "fishermens community hospital":
        "https://baptisthealth.net/-/media/Documents/Patient-Resources/Patient-Pricing/Apr-2026/821682066_fishermens-health-inc_standardcharges.zip",
    "mariners hospital":
        "https://baptisthealth.net/-/media/Documents/Patient-Resources/Patient-Pricing/Apr-2026/591987355_mariners-hospital_standardcharges.zip",
    "west kendall baptist hospital":
        "https://baptisthealth.net/-/media/Documents/Patient-Resources/Patient-Pricing/Apr-2026/522438452_west-kendall-baptist-hospital_standardcharges.zip",
    # HCA Florida: additional facilities from hcafloridahealthcare.com/cms-hpt.txt
    "oviedo medical center":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/46-4660005_OVIEDO-MEDICAL-CENTER_standardcharges.json",
    "st lucie medical center":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/62-1113740_HCA-FLORIDA-ST.-LUCIE-HOSPITAL_standardcharges.json",
    "university hospital and medical center":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/32-0583104_HCA-FLORIDA-UNIVERSITY-HOSPITAL_standardcharges.json",
    "westside regional medical center":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/75-2482249_HCA-FLORIDA-WESTSIDE-HOSPITAL_standardcharges.json",
    # Tenet Health / Palm Beach Health Network: mrfs.hyvehealthcare.com/TenetHealth/
    "delray medical center":
        "https://mrfs.hyvehealthcare.com/TenetHealth/752922687_delray-medical-center,-inc._standardcharges.json",
    "west boca medical center":
        "https://mrfs.hyvehealthcare.com/TenetHealth/752922710_west-boca-medical-center,-inc._standardcharges.json",
    "good samaritan medical center":
        "https://mrfs.hyvehealthcare.com/TenetHealth/752932824_good-samaritan-medical-center,-inc._standardcharges.json",
    "st mary's medical center":
        "https://mrfs.hyvehealthcare.com/TenetHealth/752932830_st.-marys-medical-center,-inc_standardcharges.json",
    # Healthcare System of America (Miami-Dade): each facility's own domain,
    # /pricetransparency/<ein>_hsa-*_standardcharges.csv
    "north shore medical center":
        "https://northshoremc.org/pricetransparency/995020733_hsa-nsmc-llc_standardcharges.csv",
    "palmetto general hospital":
        "https://palmettogeneral.org/pricetransparency/995002378_hsa-pgh-llc_standardcharges.csv",
    "hialeah hospital":
        "https://hialeahhosp.org/pricetransparency/995053647_hsa-hh-llc_standardcharges.csv",
    # Community Health Systems (independent FL affiliates): own domain chargemaster page
    "physicians regional medical center - pine ridge":
        "https://www.physiciansregional.com/Uploads/Public/Documents/charge-masters/charge-masters-2024/204401957_physicians-regional-healthcare-system-pine-ridge_standardcharges.csv",
    "santa rosa medical center":
        "https://www.srmcfl.com/Uploads/Public/Documents/charge-masters/charge-masters-2024/680045270_santa-rosa-medical-center_standardcharges.csv",
    # UF Health (The Villages, formerly Villages Regional Hospital / now UF
    # Health Spanish Plaines Hospital): ufhealth.org/cms-hpt.txt
    "villages regional hospital":
        "https://ufhealth.pt.panaceainc.com/MRFDownload/ufhealth/uf-villages",
    # BayCare: Winter Haven Hospital is BayCare, not AdventHealth (despite the
    # DOMAIN_HINTS guess) — found via baycare.org/cms-hpt.txt
    "winter haven hospital":
        "https://baycare.org/-/media/project/baycare/consumer-portal/billing-and-insurance/pricing-files-compressed/590724462_WinterHavenHospital_standardcharges.zip",
    # Universal Health Services (independent FL affiliates): uhsfilecdn.eskycity.net
    "manatee memorial hospital":
        "https://uhsfilecdn.eskycity.net/ac/232798290_manatee-memorial-hospital_standardcharges.csv",
    "wellington regional medical center":
        "https://uhsfilecdn.eskycity.net/ac/232306491_wellington-regional-medical-center_standardcharges.csv",
    # NCH Healthcare System (Naples, independent)
    "naples community hospital":
        "https://nchmd.org/wp-content/uploads/590694358_naples-community-hospital_standardcharges.json",
    # Jackson Health System (Miami-Dade public hospital system): PARA Price
    # Transparency Tool-hosted report, linked from jacksonhealth.org billing page
    "jackson health system":
        "https://apps.para-hcfs.com/PTT/FinalLinks/Reports.aspx",
    # HCA Florida: more facilities from hcafloridahealthcare.com/cms-hpt.txt
    "ucf lake nona hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/85-4247323_UCF-LAKE-NONA-HOSPITAL_standardcharges.json",
    # Jackson Hospital (Marianna, independent rural, unrelated to Miami's
    # Jackson Health System or Baptist Health's Doctors Hospital)
    "jackson hospital":
        "https://www.jackson-hospital.com/wp-content/uploads/2026/08/596001321_jackson-hospital_standardcharges.csv",
    # Larkin Community Hospital (Miami, independent): larkinhealth.com price
    # transparency statement links to PARA-hosted reports, same host as
    # Jackson Health System above
    "larkin community hospital palm springs campus":
        "https://apps.para-hcfs.com/PTT/FinalLinks/Reports.aspx",
    "larkin community hospital":
        "https://apps.para-hcfs.com/PTT/FinalLinks/Reports.aspx",
    # Tallahassee Memorial Healthcare (independent)
    "tallahassee memorial healthcare":
        "https://www.tmh.org/sites/default/files/price-transparency/591917016_tallahassee-memorial-hospital_standardcharges.csv",
    # Baptist Health Care (Pensacola, distinct org from Baptist Health SF and
    # Baptist Health Jacksonville): ebaptisthealthcare.org uses the same
    # panaceainc.com MRFDownload redirector pattern.
    # NOTE: "Baptist Hospital Of Miami" (ccn-100008, Baptist Health South
    # Florida) and "South Florida Baptist Hospital" (ccn-100132, BayCare,
    # Plant City) both flatten to superstrings of this pattern, so they need
    # their own longer entries below or they'd wrongly inherit the Pensacola file.
    "baptist hospital":
        "https://baptisthealthcare.pt.panaceainc.com/MRFDownload/baptisthealthcare/baptist",
    "baptist hospital of miami":
        "https://baptisthealth.net/-/media/Documents/Patient-Resources/Patient-Pricing/Apr-2026/590910342_baptist-hospital-of-miami_standardcharges.zip",
    "south florida baptist hospital":
        "https://baycare.org/-/media/project/baycare/consumer-portal/billing-and-insurance/pricing-files-compressed/590594631_SouthFloridaBaptistHospital_standardcharges.zip",
    # Independent FL hospitals (own domain, own price-transparency page)
    "parrish medical center":
        "https://www.parrishhealthcare.com/documents/596020427_north-brevard-county-hospital-dist-parrish-medical-center_standardcharges.csv",
    "lakeland regional medical center":
        "https://www.mylrh.org/wp-content/uploads/2025/04/59-2650456_LakelandRegionalMedicalCenter_standardcharges.csv",
    "desoto memorial hospital":
        "https://www.dmh.org/_files/ugd/753cbe_612bfadd8b314ba78d4741a244d074e2.csv_desoto-memorial-hospital_standardcharges.csv",
    "jupiter medical center":
        "https://www.jupitermed.com/documents/content/59-1460239_jupiter-medical-center_standardcharges.csv",
    # Holy Cross Hospital (Fort Lauderdale): Trinity Health, hpt.trinity-health.org
    "holy cross hospital":
        "https://hpt.trinity-health.org/590791028_holy-cross-hospital-ft-lauderdale_standardcharges.zip",
    # Lower Keys Medical Center (Key West): Community Health Systems affiliate,
    # own domain chargemaster page, same pattern as Santa Rosa Medical Center
    "lower keys medical center":
        "https://www.lkmc.com/Uploads/Public/Documents/charge-masters/charge-masters-2024/650905661_lower-keys-medical-center_standardcharges.csv",

    # --- FL independent batch B (ccn-100161..101315), sourced by hand from
    # each system's own cms-hpt.txt or price-transparency page. Several
    # hospitals in this range turned out to already be covered above (added
    # concurrently by another session working batch A) with identical URLs —
    # not repeated here to avoid dict-key clutter: Central Florida Lake
    # Monroe Hospital, University Hospital And Medical Center, Westside
    # Regional Medical Center, Oviedo Medical Center, St Lucie Medical
    # Center, Larkin Community Hospital (+ Palm Springs Campus), Cape Coral
    # Hospital, Gulf Coast Medical Center, Cape Canaveral Hospital, Palm Bay
    # Hospital, Mariners Hospital, West Kendall Baptist Hospital, Doctors
    # Hospital, Memorial Hospital Pembroke, Palmetto General Hospital,
    # Physicians Regional Medical Center - Pine Ridge, Villages Regional
    # Hospital.
    # CMS's "Marion Communtiy Hospital" (their spelling) is HCA's West Marion,
    # not otherwise covered above.
    "marion communtiy hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/59-1479652_HCA-FLORIDA-WEST-MARION-HOSPITAL_standardcharges.json",
    "central florida lake monroe hospital":
        "https://stctrprodsnsvc00455826e6.blob.core.windows.net/pt-final-posting-files/59-1978725_HCA-FLORIDA-LAKE-MONROE-HOSPITAL_standardcharges.json",
    # batch A's "fishermens community hospital" (no apostrophe) never matches
    # CMS's actual "Fishermen'S Community Hospital" — the flattened facility
    # name has a space where the apostrophe was, so it needs an apostrophe
    # here too to line up after flattening.
    "fishermen's community hospital":
        "https://baptisthealth.net/-/media/Documents/Patient-Resources/Patient-Pricing/Apr-2026/821682066_fishermens-health-inc_standardcharges.zip",

    # Baptist Health Care (Pensacola): panaceainc.com MRFDownload redirector
    "gulf breeze hospital":
        "https://baptisthealthcare.pt.panaceainc.com/MRFDownload/baptisthealthcare/glfbreeze",
    "jay hospital":
        "https://baptisthealthcare.pt.panaceainc.com/MRFDownload/baptisthealthcare/jay",

    # Nicklaus Children's (Miami)
    "nicklaus children's hospital":
        "https://www.nicklauschildrens.org/NCH/media/docs/pdf/Finance/590638499_1871540237_nicklaus-childrens-hospital_standardcharges.zip",

    # Miami Jewish Health (Douglas Gardens Hospital)
    "douglas gardens hospital":
        "https://www.miamijewishhealth.org/wp-content/uploads/2026/07/590624414_DouglesGardensHospital_standardcharges.csv",

    # Health Care District of Palm Beach County (Lakeside Medical Center,
    # Belle Glade) — panaceainc.com MRFDownload redirector; the domain hint
    # bayfronthealth.com is unrelated and wrong
    "lakeside medical center":
        "https://hcdpbc.pt.panaceainc.com/MRFDownload/hcdpbc/hcdpbc",

    # Small rural/critical-access independents, each with its own portal
    "lake butler hospital":
        "https://lakebutlerhospital.com/wp-content/uploads/2026/04/593218382_medlink-management-services-inc.-_standardcharges.csv",
    "weems memorial hospital":
        "https://www.weemsmemorial.com/wp-content/uploads/2024/11/061766026_george-e-weems-memorial-hospital_standardcharges.csv",
    "northwest florida community hospital":
        "https://northwestfloridacommunityhospital.pg.quadax.revenuemasters.com/cdm-files/596002711_northwest-florida-community-hospital_standardcharges.csv",
    "hendry regional medical center":
        "https://hospitalpricetransparencyfiles.com/hendry-county-hospital-authority/596002318_Hendry-County-Hospital-Authority_standardcharges.csv",
    "madison county memorial hospital":
        "https://madisoncounty.pg.quadax.revenuemasters.com/cdm-files/592319288_madison-county-memorial-hospital_standardcharges.csv",
}
