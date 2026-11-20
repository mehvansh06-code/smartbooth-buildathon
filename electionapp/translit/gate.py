# -*- coding: utf-8 -*-
"""Reject anything that reads as an English word rather than a name.

This is the check Ward 82 did not have. Its filter was seven words --
{'minister','brown','soft','buried','goddess','god','sea'} -- which is exactly why
Pistachio (पिस्ता), Sunlight (सूर्यप्रकाश), Pacific (प्रशांत), Avoidance (परिहार),
Bottle Gourd (घीया) and Canal (नहर) went to press on real voter slips.

Nothing here translates, so a hit should be impossible; the gate exists so that if one
ever appears it is caught loudly instead of being printed on someone's polling slip.
"""

# Common English words a translation engine produces from Indian given names and surnames.
ENGLISH_WORDS = set("""
pistachio sunlight sun moon star sky cloud rain snow light bright dark shadow
pacific atlantic avoidance avoid gourd bottle canal river ocean sea lake pond
pulled pull cast chain range straight success direction aura flows flow madam
chimney sugar salt cover per menu satan devil fool foolish goody bevy self
poker talk talks jane style ascending aspirational minister brown soft buried
goddess god saffron sir madam mister lord king queen prince princess
gold golden silver iron copper diamond pearl ruby emerald jewel
flower rose lotus jasmine garden tree leaf branch root seed fruit apple mango
water fire earth wind air stone rock sand soil dust
love peace joy happy happiness sorrow anger fear brave brave courage
lion tiger elephant horse cow bird eagle snake fish deer
red blue green yellow white black grey orange purple pink
mother father brother sister son daughter uncle aunt family friend
morning evening night day week month year season spring summer winter autumn
honey sweet bitter sour spicy milk butter cream sugar rice wheat
victory defeat war battle soldier army king kingdom crown throne
temple church prayer worship faith religion soul spirit heaven hell
money wealth rich poor market shop trade business work labour
teacher student school book page word letter language story song music dance
beautiful pretty handsome ugly good bad great small large big tiny
first second third last next previous
health sick medicine doctor nurse hospital
truth lie honest cheat thief police court judge law
speed slow fast quick late early
wave ocean tide storm thunder lightning rainbow
cotton silk wool cloth dress shirt shoe
door window house home room floor wall roof
road path street bridge village town city country nation
knowledge wisdom mind heart body hand foot eye ear nose mouth
""".split())

# Words that are also perfectly ordinary Indian names. These must never be rejected.
INDIAN_NAME_WHITELIST = set("""
raj ram rama ravi anil sunil kiran prince rose honey lal mal sona mona bela
sita gita geeta amar anand arti aarti asha bharat chand hari indra jai kali
kamal karan lata maya mina meena nina pooja puja rani renu rita sagar sonu
suman tara uma veena vijay vinod deepak dinesh mahesh rakesh suresh ganesh
naresh mukesh jagdish shiv shiva krishna kishan gopal mohan sohan rohan
bhagwan devi dev deva pyare payal komal kajal sheetal neha isha usha nisha
manju sanju babu bablu golu chotu raju kaju laxmi lakshmi durga parvati
prem preeti priya kavita savita sarita anita sunita namita smita
arjun karna bhim nakul sahdev yudhisthir
madan mangal budh guru shukra shani surya chandra
""".split())


def looks_english(token):
    """True if this English spelling reads as a dictionary word rather than a name."""
    t = (token or '').strip().lower()
    if not t or t in INDIAN_NAME_WHITELIST:
        return False
    return t in ENGLISH_WORDS


def check_name(name_en):
    """Every offending word in a full name, or [] if it is clean."""
    return [w for w in (name_en or '').split() if looks_english(w)]
