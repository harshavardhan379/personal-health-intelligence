"""Everything decided in benchmarks/personal_health_bench/README.md, as data.

If this file and that spec disagree, fix this file or record a new decision there. Nothing
here is random: it is the fixed vocabulary the seeded generator draws from.
"""

from dataclasses import dataclass, field
from datetime import date

DATASET_VERSION = "1"
GENERATOR_VERSION = "0.1.0"
DEFAULT_SEED = 2026

N_PATIENTS = 50
N_DEV = 30  # patients 1..30 are development, 31..50 are the locked test split

# Fixed calendar window (decision 12). Never relative to today: the extraction validator
# rejects future dates, so a today-relative dataset could silently become invalid.
WINDOW_START = date(2023, 1, 1)
WINDOW_END = date(2026, 6, 30)


# --- labs (restricted to what the current lab extractor can parse) -----------------


@dataclass(frozen=True)
class LabSpec:
    name: str
    unit: str
    ref_range: str
    decimals: int
    aliases: tuple = ()


LABS = {
    lab.name: lab
    for lab in (
        LabSpec("HbA1c", "%", "4.0-5.6", 1, ("HBA1C", "Hb A1c", "A1c")),
        LabSpec("Glucose", "mg/dL", "70-99", 0, ("Fasting Glucose", "Blood Glucose")),
        LabSpec("Total Cholesterol", "mg/dL", "0-200", 0, ("Cholesterol", "Total Chol")),
        LabSpec("LDL", "mg/dL", "0-100", 0, ("LDL Cholesterol", "LDL-C")),
        LabSpec("Creatinine", "mg/dL", "0.6-1.3", 1, ("Serum Creatinine",)),
        LabSpec("Systolic BP", "mmHg", "90-120", 0, ("Systolic Blood Pressure", "SBP")),
        LabSpec("Hemoglobin", "g/dL", "12.0-17.5", 1, ("Hb", "Haemoglobin")),
    )
}


# --- medications (dose ladders: one step per change) -------------------------------


@dataclass(frozen=True)
class MedSpec:
    name: str
    doses: tuple  # ladder, lowest first; written exactly as the document prints them
    frequency: str
    aliases: tuple = ()  # brand names


MEDS = {
    med.name: med
    for med in (
        MedSpec("Metformin", ("500mg", "850mg", "1000mg"), "twice daily", ("Glucophage",)),
        MedSpec("Glipizide", ("2.5mg", "5mg", "10mg"), "once daily", ("Glucotrol",)),
        MedSpec("Insulin glargine", ("10IU", "14IU", "20IU"), "once daily", ("Lantus",)),
        MedSpec("Lisinopril", ("5mg", "10mg", "20mg"), "once daily", ("Zestril",)),
        MedSpec("Atorvastatin", ("10mg", "20mg", "40mg", "80mg"), "once daily", ("Lipitor",)),
        MedSpec("Amoxicillin", ("500mg",), "three times daily", ("Amoxil",)),
    )
}

SYMPTOMS = (
    "headache", "dizziness", "fatigue", "blurred vision", "cough", "fever", "sore throat",
)
ENCOUNTER_TYPES = (
    "Annual Physical", "Follow-up Visit", "Specialist Consultation",
    "Hospital Admission", "Hospital Discharge",
)
FACILITIES = ("Riverside Clinic", "Northgate Medical Center", "Lakeview Hospital")


# --- name aliases (framework decision 8) ---------------------------------------------


def alias_table() -> dict:
    """canonical name -> sorted aliases. Shipped with the dataset as aliases.json and used
    by the scorer, identically for every system."""
    table = {}
    for lab in LABS.values():
        table[lab.name] = sorted(lab.aliases)
    for med in MEDS.values():
        table[med.name] = sorted(med.aliases)
    return table


def _build_alias_index() -> dict:
    index = {}
    for canonical, aliases in alias_table().items():
        for name in (canonical, *aliases):
            index[name.strip().lower()] = canonical.strip().lower()
    return index


_ALIAS_INDEX = _build_alias_index()


def canonical_name(raw: str) -> str:
    """Normalise a name for matching: trim, case-fold, then map known aliases to the
    (case-folded) canonical name. Unknown names are returned trimmed and case-folded, so
    two names match exactly when canonical_name() of both is equal."""
    key = raw.strip().lower()
    return _ALIAS_INDEX.get(key, key)


# --- archetypes (decisions 3, 10, 12) ------------------------------------------------


@dataclass(frozen=True)
class Archetype:
    key: str
    label: str
    docs: dict  # doc_type -> count
    history_days: tuple  # (min, max) length of the patient's history
    gap_days: tuple  # (min, max) days between consecutive reports
    reuploads: int = 0  # extra duplicate uploads on top of docs

    @property
    def total_docs(self) -> int:
        return sum(self.docs.values()) + self.reuploads


ARCHETYPES = (
    Archetype("improving_chronic", "Improving chronic",
              {"lab_report": 3, "prescription": 2, "visit_note": 1}, (270, 450), (76, 104)),
    Archetype("worsening_chronic", "Worsening chronic",
              {"lab_report": 3, "prescription": 2, "visit_note": 1, "discharge_summary": 1},
              (270, 450), (76, 104)),
    Archetype("stable", "Stable",
              {"lab_report": 3, "prescription": 1}, (360, 540), (130, 170)),
    Archetype("contradiction", "Contradiction",
              {"lab_report": 2, "prescription": 2, "discharge_summary": 1}, (120, 240), (30, 120)),
    Archetype("missing_information", "Missing information",
              {"lab_report": 1, "prescription": 1, "visit_note": 1}, (90, 180), (14, 90)),
    Archetype("duplicate_upload", "Duplicate upload",
              {"lab_report": 2, "prescription": 1}, (90, 180), (60, 120), reuploads=1),
    Archetype("acute_episode", "Acute episode",
              {"lab_report": 1, "prescription": 1, "discharge_summary": 1}, (18, 24), (2, 9)),
    Archetype("multi_document", "Multi-document",
              {"lab_report": 2, "prescription": 1, "visit_note": 1, "discharge_summary": 1,
               "combined": 2}, (240, 420), (30, 90)),
)
ARCHETYPE_BY_KEY = {a.key: a for a in ARCHETYPES}


# --- ids, split and round-robin assignment -------------------------------------------


def patient_id(n: int) -> str:
    return f"p{n:03d}"


def document_id(n: int, index: int) -> str:
    return f"{patient_id(n)}-d{index:02d}"


def event_id(n: int, index: int) -> str:
    return f"{patient_id(n)}-e{index:03d}"


def split_for(n: int) -> str:
    return "dev" if n <= N_DEV else "test"


def archetype_for(n: int) -> Archetype:
    return ARCHETYPES[(n - 1) % len(ARCHETYPES)]


# --- document degradation (decision 9) -----------------------------------------------


@dataclass(frozen=True)
class NoiseProfile:
    version: str = "1"
    # per document
    layout_shares: dict = field(
        default_factory=lambda: {"key_value": 0.50, "table": 0.25, "prose": 0.25}
    )
    # 10/01/2026 is excluded on purpose: ambiguous (day/month vs month/day).
    date_styles: tuple = ("iso", "day_mon_year", "month_day_year")
    two_page_share: float = 0.15
    # Share of PATIENTS whose documents have no header date at all. NOT in the approved
    # list of rates: an assumption made while writing this file, to be confirmed.
    no_header_patient_share: float = 0.15
    # per event, drawn independently (approved rates)
    date_omitted_from_line: float = 0.10
    month_only: float = 0.05
    undated: float = 0.03
    unit_omitted: float = 0.05
    dose_omitted: float = 0.05
    name_variant: float = 0.10


NOISE = NoiseProfile()
