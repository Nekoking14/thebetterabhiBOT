"""Editable personas, skill groups and conservative title matching."""
import re
import unicodedata
from difflib import SequenceMatcher

PERSONAS = {
    "IT Decision Makers": ["CIO", "Chief Information Officer", "VP IT", "VP Information Technology",
                           "IT Director", "Director of IT", "Head of IT", "Head of Technology"],
    "Infrastructure": ["Head of Infrastructure", "Infrastructure Manager", "IT Infrastructure Manager",
                       "Director of Infrastructure", "Systems Manager", "Network Manager"],
    "IT Operations": ["IT Operations Manager", "Head of IT Operations", "IT Manager", "Systems Manager"],
    "Security": ["CISO", "Head of Security", "IT Security Manager", "Security Operations Manager",
                 "Cyber Security Manager"],
    "Service Desk": ["Service Desk Manager", "Help Desk Manager", "IT Support Manager",
                     "End User Computing Manager"],
    "Custom": [],
}
SKILL_GROUPS = {
    "Endpoint Management": ["Endpoint Management", "Microsoft Intune", "Intune", "SCCM",
                            "Microsoft Configuration Manager", "Jamf", "Device Management", "MDM", "RMM"],
    "IT Infrastructure": ["Windows Server", "Active Directory", "Microsoft Entra ID", "Entra ID",
                          "Azure", "VMware", "Hyper-V", "Networking", "Infrastructure Management"],
    "IT Operations": ["IT Operations", "Systems Administration", "IT Management", "Infrastructure Operations"],
    "Automation": ["PowerShell", "Scripting", "Automation", "Python", "IT Automation"],
    "ITSM / Service Desk": ["ServiceNow", "Jira Service Management", "ITIL", "Help Desk", "Service Desk"],
    "Security": ["Endpoint Security", "Vulnerability Management", "Patch Management",
                 "Cybersecurity", "Microsoft Defender"],
    "Microsoft Ecosystem": ["Microsoft 365", "Azure", "Windows", "Intune", "Microsoft Intune",
                            "Entra ID", "Microsoft Entra ID", "Active Directory"],
    "General Delivery": ["Project Management", "Agile", "Scrum", "Business Analysis"],
}
SKILL_ALIASES = {
    "microsoft intune": "intune", "microsoft entra id": "entra id",
    "microsoft configuration manager": "sccm", "office 365": "microsoft 365",
    "cyber security": "cybersecurity",
    "configuration manager": "sccm", "configmgr": "sccm", "defender": "microsoft defender",
    "remote monitoring and management": "rmm",
}
TITLE_SIMILARITY_THRESHOLD = 0.90
WEAK_IT_TITLES = ["Systems Administrator", "Network Administrator", "IT Administrator"]
ENTRY_IT_TITLES = ["IT Support Specialist", "Service Desk Analyst", "IT Support Technician"]

# Authority is explicit and independent of fuzzy filtering or custom titles.
# Each role lists tier, title-fit fraction and purchasing-influence fraction.
PERSONA_TIERS = {
    1: "Strategic Decision Maker",
    2: "Strong Technical Buyer / Influencer",
    3: "Operational Practitioner",
    4: "Weak / Irrelevant",
}
AUTHORITY_ROLES = [
    ("CIO", 1, 1.0, 1.0), ("Chief Information Officer", 1, 1.0, 1.0),
    ("CISO", 1, 1.0, 1.0),
    *[(t, 1, 1.0, 0.9) for t in PERSONAS["IT Decision Makers"] if t not in {"CIO", "Chief Information Officer"}],
    ("Head of Infrastructure", 2, 0.9, 0.8), ("Director of Infrastructure", 2, 0.9, 0.8),
    ("Head of IT Operations", 2, 0.9, 0.8),
    ("IT Infrastructure Manager", 2, 0.85, 0.7), ("Infrastructure Manager", 2, 0.85, 0.7),
    ("IT Operations Manager", 2, 0.85, 0.7), ("IT Manager", 2, 0.8, 0.7),
    ("Systems Manager", 2, 0.75, 0.6), ("Network Manager", 2, 0.75, 0.6),
    ("Head of Security", 2, 0.9, 0.8),
    *[(t, 2, 0.85, 0.7) for t in PERSONAS["Security"] if t not in {"CISO", "Head of Security"}],
    *[(t, 3, 0.55, 0.5) for t in PERSONAS["Service Desk"]],
    *[(t, 3, 0.5, 0.3) for t in WEAK_IT_TITLES],
    *[(t, 4, 1 / 6, 0.1) for t in ENTRY_IT_TITLES + ["Help Desk Analyst"]],
]


def classify_authority(title):
    """Classify explicit normalized roles; custom/fuzzy matches cannot buy authority.

    Tier 1 is the highest tier. Extra regional/scope qualifiers are allowed,
    but junior, assisting and temporary-learning roles never inherit authority.
    """
    normalized = normalize_title(title)
    words = set(normalized.split())
    if words & {"junior", "intern", "trainee", "student", "recruiter", "assistant", "consultant"}:
        return {"tier": 4, "title_fit": 0, "influence": 0, "role": None}
    role_words = {"chief", "president", "director", "head", "manager", "administrator", "specialist", "analyst"}
    # Match scoped roles before generic Head of IT / IT Manager to avoid
    # inflating Head of IT Operations or IT Support Manager into strategic roles.
    roles = sorted(AUTHORITY_ROLES, key=lambda item: -len(normalize_title(item[0]).split()))
    for role, tier, fit, influence in roles:
        expected = set(normalize_title(role).split())
        if expected <= words and words & role_words == expected & role_words:
            return {"tier": tier, "title_fit": fit, "influence": influence, "role": role}
    return {"tier": 4, "title_fit": 0, "influence": 0, "role": None}


def normalize_text(value):
    value = unicodedata.normalize("NFKD", value or "").casefold()
    value = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", value).split())


def normalize_title(title):
    title = normalize_text(title)
    for short, long in (("cio", "chief information officer"), ("ciso", "chief information security officer"),
                        ("vp", "vice president"), ("it", "information technology"),
                        ("infra", "infrastructure")):
        title = re.sub(r"\b" + short + r"\b", long, title)
    return " ".join(word for word in title.split() if word not in {"of", "the", "and"})


def title_matches(title, target):
    """Match reordered roles and minor variations without accepting unrelated roles.

    A role may carry qualifiers (e.g. EMEA); roles assisting a decision maker do
    not inherit that decision maker's persona. IT qualifiers may be omitted for
    explicit infrastructure/security/operations roles.
    """
    left, right = normalize_title(title), normalize_title(target)
    if not left or not right:
        return False
    if re.search(r"\b(assistant|intern|trainee)\b", left) and not re.search(
            r"\b(assistant|intern|trainee)\b", right):
        return False
    a, b = set(left.split()), set(right.split())
    role_words = {"chief", "president", "director", "head", "manager", "administrator", "specialist", "analyst"}
    if a & role_words != b & role_words:
        return False
    if a == b or b <= a:
        return True
    # Explicit IT Infrastructure Manager also matches Infrastructure Manager.
    if a - {"information", "technology"} == b - {"information", "technology"}:
        return True
    return SequenceMatcher(None, " ".join(sorted(a)), " ".join(sorted(b))).ratio() >= TITLE_SIMILARITY_THRESHOLD


def matching_personas(title):
    # Prefer specific functional groups over a generic IT Manager / Head of IT.
    # Membership is unchanged for filters; scoring uses the first actual persona.
    matches = []
    for name, titles in PERSONAS.items():
        lengths = [len(normalize_title(t).split()) for t in titles if title_matches(title, t)]
        if lengths:
            matches.append((name, max(lengths)))
    return [name for name, _ in sorted(matches, key=lambda item: -item[1])]


def normalize_skill(skill):
    text = normalize_text(skill)
    return SKILL_ALIASES.get(text, text)


def match_skills(skills, groups=None):
    """Return unique canonical evidence; aliases/overlapping groups never double count."""
    groups = list(SKILL_GROUPS) if groups is None or not groups else list(groups)
    unknown = set(groups) - set(SKILL_GROUPS)
    if unknown:
        raise ValueError("Unknown skill groups: " + ", ".join(sorted(unknown)))
    allowed = {normalize_skill(skill) for group in groups for skill in SKILL_GROUPS[group]}
    found = {}
    for skill in skills or []:
        canonical = normalize_skill(skill)
        if canonical in allowed:
            found.setdefault(canonical, skill)
    return list(found.values())
