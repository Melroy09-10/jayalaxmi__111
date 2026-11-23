"""Does an address name a city, a state or a country? (India-focused list; a 6-digit pincode also counts.)"""
import re

STATES = """andhra pradesh|arunachal pradesh|assam|bihar|chhattisgarh|goa|gujarat|haryana|himachal pradesh|jharkhand|karnataka|
kerala|madhya pradesh|maharashtra|manipur|meghalaya|mizoram|nagaland|odisha|orissa|punjab|rajasthan|sikkim|tamil nadu|tamilnadu|
telangana|tripura|uttar pradesh|uttarakhand|west bengal|andaman and nicobar|chandigarh|dadra and nagar haveli|daman and diu|
delhi|new delhi|jammu and kashmir|jammu|kashmir|ladakh|lakshadweep|puducherry|pondicherry"""
COUNTRIES = "india|nepal|bhutan|sri lanka|bangladesh|uae|dubai|usa|uk|singapore|malaysia"
CITIES = """mumbai|pune|nagpur|nashik|thane|navi mumbai|aurangabad|solapur|kolhapur|sion|dadar|andheri|borivali|kurla|vashi|
bengaluru|bangalore|mysuru|mysore|mangaluru|mangalore|udupi|hubballi|hubli|dharwad|belagavi|belgaum|davanagere|davangere|ballari|bellary|
shivamogga|shimoga|tumakuru|tumkur|kalaburagi|gulbarga|vijayapura|bijapur|hassan|mandya|chitradurga|raichur|bidar|karwar|kundapura|kundapur|
manipal|mulki|chikkamagaluru|chikmagalur|madikeri|bagalkot|gadag|haveri|koppal|ramanagara|kolar|chikkaballapur|
chennai|coimbatore|madurai|tiruchirappalli|trichy|salem|tirunelveli|erode|vellore|tiruppur|thoothukudi|
hyderabad|secunderabad|warangal|vijayawada|visakhapatnam|vizag|guntur|tirupati|nellore|kurnool|
kochi|cochin|thiruvananthapuram|trivandrum|kozhikode|calicut|thrissur|kannur|kollam|palakkad|
ahmedabad|surat|vadodara|baroda|rajkot|bhavnagar|jamnagar|
jaipur|jodhpur|udaipur|kota|ajmer|bikaner|
delhi|gurugram|gurgaon|noida|ghaziabad|faridabad|lucknow|kanpur|agra|varanasi|prayagraj|allahabad|meerut|
bhopal|indore|jabalpur|gwalior|ujjain|raipur|bilaspur|
kolkata|calcutta|howrah|siliguri|patna|ranchi|jamshedpur|bhubaneswar|cuttack|guwahati|
ludhiana|amritsar|jalandhar|chandigarh|dehradun|panaji|margao|vasco|surat|hosur|ongole|rajahmundry|uttara kannada|dakshina kannada|uttar kannada|south kannada|north kannada|kodagu|coorg|chikkamagaluru|dharwad|bengaluru urban|bengaluru rural|ankola|kumta|honnavar|bhatkal|sirsi|yellapur|haliyal|dandeli|joida|siddapur|mundgod|gokarna|byndoor|baindur|kota|brahmavar|karkala|hebri|kapu|padubidri|moodbidri|puttur|sullia|bantwal|belthangady|ullal|surathkal|panambur|
sagara|sagar|bhadravathi|thirthahalli|sorab|shikaripura|hosanagara|honnali|channagiri|harihar|ranebennur|hirekerur|savanur|shiggaon|hangal|navalgund|kundgol|athani|gokak|chikodi|bailhongal|saundatti|ramdurg|mudhol|jamkhandi|badami|ilkal|hungund|
arsikere|belur|sakleshpur|channarayapatna|holenarasipur|nanjangud|hunsur|periyapatna|kushalnagar|virajpet|somwarpet|chamarajanagar|kollegal|gundlupet|t narasipura|krishnarajanagara|srirangapatna|maddur|malavalli|pandavapura|nagamangala|kunigal|tiptur|sira|pavagada|madhugiri|
doddaballapur|devanahalli|hosakote|nelamangala|anekal|magadi|channapatna|kanakapura|bidadi|chintamani|sidlaghatta|gauribidanur|bagepalli|mulbagal|bangarpet|malur|srinivaspur|
gangavathi|sindhanur|manvi|lingsugur|yadgir|shahapur|sedam|aland|afzalpur|jewargi|basavakalyan|bhalki|humnabad|aurad|
mahabaleshwar|ratnagiri|chiplun|sawantwadi|kudal|malvan|sangli|satara|karad|ichalkaranji|jalgaon|dhule|latur|nanded|akola|amravati|panvel|kalyan|bhiwandi|vasai|virar|mira road|
mapusa|ponda|bicholim|canacona|quepem|kasaragod|kanhangad|payyanur|vadakara|manjeshwar|kumbla|mananthavady|kalpetta|alappuzha|kottayam|pathanamthitta|idukki|munnar|ernakulam|angamaly|aluva|perumbavoor"""

def _re(words):
    items = sorted({w.strip() for w in words.replace("\n", "").split("|") if w.strip()}, key=len, reverse=True)
    return re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(i) for i in items) + r")(?![a-z])")

_ALL = _re(STATES + "|" + COUNTRIES + "|" + CITIES)

def has_place(address):
    """True when the address mentions a known city / state / country, or contains a 6-digit pincode."""
    text = (address or "").lower()
    return bool(_ALL.search(text) or re.search(r"(?<!\d)\d{6}(?!\d)", text))


def is_place_word(word):
    """True when a single word is (part of) a known place name, so the spell check leaves it alone."""
    w = word.lower()
    return any(w in name.split() for name in _NAMES)

_NAMES = {n.strip() for n in (STATES + "|" + COUNTRIES + "|" + CITIES).replace(chr(10), "").split("|") if n.strip()}
