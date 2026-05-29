import os

MODEL = os.getenv("STREETSENSE_MODEL", "gemma4:latest")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

TORONTO_BBOX = {
    "lat_min": 43.58,
    "lat_max": 43.86,
    "lng_min": -79.64,
    "lng_max": -79.11,
}

# Clustering parameters
CLUSTER_RADIUS_M = 300          # metres — events within this radius form a candidate cluster
CLUSTER_WINDOW_HOURS = 1        # hours — events within this window are considered related
CLUSTER_WINDOW_MINUTES = 60     # same as above in minutes (used by geo_tools)
FLOOD_CLUSTER_WINDOW_HOURS = 3  # hours — citywide flood pass groups flood events across any distance

# Pipeline thresholds
MIN_CONFIDENCE_THRESHOLD = 0.6  # minimum LLM confidence to treat correlation as causal
MIN_SEVERITY_FOR_BRIEF = 4      # minimum severity score to generate an operational brief
MAX_AGENT_ITERATIONS = 10       # circuit breaker ceiling for pipeline nodes

# Geocoding
GEOCODE_DELAY_SECONDS = float(os.getenv("GEOCODE_DELAY", "1.0"))
NOMINATIM_RATE_LIMIT = 1.0      # seconds between Nominatim calls (policy)
# Toronto major street centroids for demo fallback
STREET_COORDS = {
    # Core downtown grid
    "bathurst": (43.6555, -79.4111),
    "king": (43.6441, -79.3989),
    "queen": (43.6510, -79.3795),
    "spadina": (43.6544, -79.4040),
    "dundas": (43.6545, -79.4195),
    "bloor": (43.6662, -79.4114),
    "college": (43.6606, -79.4000),
    "wellesley": (43.6650, -79.3860),
    "yonge": (43.6700, -79.3870),
    "avenue": (43.6800, -79.4100),
    # East downtown / east end
    "parliament": (43.6590, -79.3640),
    "sherbourne": (43.6640, -79.3720),
    "jarvis": (43.6560, -79.3730),
    "church": (43.6570, -79.3760),
    "bay": (43.6700, -79.3840),
    "university": (43.6580, -79.3920),
    "victoria": (43.6540, -79.3790),
    "mutual": (43.6560, -79.3750),
    "ontario": (43.6600, -79.3680),
    "berkeley": (43.6520, -79.3600),
    "parliament": (43.6590, -79.3640),
    "broadview": (43.6670, -79.3540),
    "pape": (43.6730, -79.3450),
    "jones": (43.6720, -79.3390),
    "greenwood": (43.6760, -79.3330),
    "coxwell": (43.6800, -79.3240),
    "woodbine": (43.6830, -79.3140),
    "kingston": (43.6900, -79.2970),
    "victoria park": (43.6970, -79.2790),
    "warden": (43.7150, -79.2560),
    # West end
    "dufferin": (43.6570, -79.4390),
    "ossington": (43.6580, -79.4270),
    "dovercourt": (43.6590, -79.4340),
    "lansdowne": (43.6570, -79.4470),
    "roncesvalles": (43.6480, -79.4500),
    "parkside": (43.6490, -79.4560),
    "keele": (43.6650, -79.4640),
    "dundas west": (43.6545, -79.4195),
    "jane": (43.6880, -79.4870),
    "runnymede": (43.6500, -79.4730),
    "high park": (43.6465, -79.4637),
    "annette": (43.6620, -79.4720),
    "pacific": (43.6600, -79.4500),
    "indian road": (43.6520, -79.4620),
    "windermere": (43.6490, -79.4680),
    "dunn": (43.6390, -79.4390),
    # North / midtown
    "st clair": (43.6876, -79.3950),
    "davenport": (43.6780, -79.4090),
    "dupont": (43.6740, -79.4060),
    "st george": (43.6680, -79.3990),
    "bedford": (43.6720, -79.4020),
    "huron": (43.6640, -79.3970),
    "harbord": (43.6620, -79.4020),
    "sussex": (43.6600, -79.4100),
    "christie": (43.6660, -79.4210),
    "manning": (43.6620, -79.4140),
    "clinton": (43.6600, -79.4120),
    "shaw": (43.6560, -79.4200),
    "lippincott": (43.6610, -79.4150),
    "palmerston": (43.6630, -79.4130),
    "euclid": (43.6530, -79.4140),
    "clinton": (43.6600, -79.4120),
    "montrose": (43.6570, -79.4160),
    "grace": (43.6550, -79.4170),
    "foxley": (43.6510, -79.4180),
    "eglinton": (43.7071, -79.3980),
    "lawrence": (43.7290, -79.4000),
    "wilson": (43.7500, -79.4150),
    "sheppard": (43.7730, -79.4100),
    "finch": (43.7960, -79.4120),
    "steeles": (43.8170, -79.4130),
    # Waterfront / south
    "lakeshore": (43.6290, -79.3810),
    "queens quay": (43.6390, -79.3780),
    "front": (43.6450, -79.3800),
    "wellington": (43.6470, -79.3830),
    "esplanade": (43.6460, -79.3700),
    "cherry": (43.6430, -79.3540),
    "carlaw": (43.6650, -79.3500),
    "leslie": (43.6990, -79.3390),
    "don mills": (43.7310, -79.3380),
    # Etobicoke
    "kipling": (43.6370, -79.5350),
    "islington": (43.6460, -79.5230),
    "royal york": (43.6490, -79.5110),
    "the kingsway": (43.6530, -79.5010),
    "kingsway": (43.6530, -79.5010),
    "bloor west": (43.6530, -79.4900),
    "dixie": (43.6340, -79.5590),
    "burnhamthorpe": (43.6440, -79.5440),
    "dundas etobicoke": (43.7120, -79.5530),
    # Scarborough
    "ellesmere": (43.7690, -79.2580),
    "lawrence east": (43.7290, -79.2500),
    "morningside": (43.7780, -79.2210),
    "markham road": (43.7850, -79.2390),
    "mccowan": (43.7700, -79.2620),
    "brimley": (43.7600, -79.2700),
    "kennedy": (43.7280, -79.2620),
    "midland": (43.7540, -79.2760),
    "scarborough golf club": (43.7690, -79.2320),
}