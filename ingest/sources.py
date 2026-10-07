"""Catalogue of public data sources used by the ingestion pipeline.

Every raw file we parse is listed here with the dataset page it came from, so that
each record in the database can be traced back to a citable public URL.
"""

# Original government portals that the OpenCity CSVs were exported from.
BBMP_WORKS_BILL_PUBLIC_VIEW = "https://account.bbmpgov.in/PublicView/?l=1"
BBMP_IFMS = "https://account.bbmpgov.in/vsswb/"
KPPP_PORTAL = "https://kppp.karnataka.gov.in/"
KPPP_API = "https://kppp.karnataka.gov.in/supplier-registration-service/v1/api/portal-service"

OC = "https://data.opencity.in/dataset"

# kind:
#   bbmp_publicview  - export of BBMP "Works Bill Public View" (rich: dates, bill type, status)
#   oc_wodetails     - OpenCity scrape of BBMP IFMS work orders/payments (job</a>desc, BR/RTGS)
#   bbmp_dept        - BBMP departmental work-order register 2010-18 (job, WO no/date, amounts)
#   bbmp_billreg     - BBMP bill register Apr-Sep 2018
#   bbmp_jobcodes    - BBMP job codes Apr-Sep 2018 (estimate amount per job code)
#   bbmp_nagarothana - Nagarothana scheme payments 2016-19
# regime: ward numbering in force for the file ("198", "225", "243", "dept", None)
SOURCES = [
    dict(file="ward151_2013_22.csv", kind="bbmp_publicview", regime="198",
         name="BBMP Work Orders - Koramangala (Ward 151), 2013-2022",
         page=f"{OC}/bbmp-work-orders-by-ward-2013-2022",
         url=f"{OC}/d22cea6d-5256-43af-8ad5-68db551e0760/resource/1ccbab74-ca87-465f-826b-5fd1b4b81a5f/download/f68edd0d-7e8b-4386-89cc-a7c946785c8a.csv"),
    dict(file="ward147_2013_22.csv", kind="bbmp_publicview", regime="198",
         name="BBMP Work Orders - Adugodi (Ward 147), 2013-2022",
         page=f"{OC}/bbmp-work-orders-by-ward-2013-2022",
         url=f"{OC}/d22cea6d-5256-43af-8ad5-68db551e0760/resource/9781e788-6c7b-4978-a600-abf3836b2a37/download/e51f4570-bde5-4fa7-bdd7-ec131041ea35.csv"),
    dict(file="ward148_2013_22.csv", kind="bbmp_publicview", regime="198",
         name="BBMP Work Orders - Ejipura (Ward 148), 2013-2022",
         page=f"{OC}/bbmp-work-orders-by-ward-2013-2022",
         url=f"{OC}/d22cea6d-5256-43af-8ad5-68db551e0760/resource/bcc85853-64d8-4ffb-b213-ff1c769f1ddf/download/ba583625-49c9-4e9f-89b4-764b9e9773cd.csv"),
    dict(file="ward172_2013_22.csv", kind="bbmp_publicview", regime="198",
         name="BBMP Work Orders - Madivala (Ward 172), 2013-2022",
         page=f"{OC}/bbmp-work-orders-by-ward-2013-2022",
         url=f"{OC}/d22cea6d-5256-43af-8ad5-68db551e0760/resource/10049b28-f6c5-4cf9-a6d4-3c5d43992f7f/download/a69d2761-7cc9-49e5-9554-aac34abc39ce.csv"),
    dict(file="ward173_2013_22.csv", kind="bbmp_publicview", regime="198",
         name="BBMP Work Orders - Jakkasandra (Ward 173), 2013-2022",
         page=f"{OC}/bbmp-work-orders-by-ward-2013-2022",
         url=f"{OC}/d22cea6d-5256-43af-8ad5-68db551e0760/resource/8e25ff56-6431-4322-9323-8c3da1b63f4a/download/d96baefa-f4f3-4ada-9765-1a5759b36f37.csv"),
    dict(file="wo_2022_23.csv", kind="oc_wodetails", regime="198",
         name="BBMP Work Orders 2022-23",
         page=f"{OC}/bbmp-work-orders-2022-23",
         url=f"{OC}/57ab37f1-b93e-40be-b553-c528bfcbf12d/resource/22b046a2-9126-46e9-901b-60ac62175844/download/c1088eec-7750-4f93-954e-486bbb78d355.csv"),
    dict(file="wo_2023_24.csv", kind="oc_wodetails", regime="198",
         name="BBMP Work Orders 2023-24",
         page=f"{OC}/bbmp-work-orders-2023-24",
         url=f"{OC}/356bfc9c-6abe-4d16-b719-a90e265c2e28/resource/375708e4-be2e-4c35-bc12-edbacedc2ab2/download/3cd0f9a1-a899-46a8-81e4-b79834ad8408.csv"),
    dict(file="wop_2024_25_198.csv", kind="oc_wodetails", regime="198",
         name="BBMP Work Orders and Payments 2024-25 (198-ward regime)",
         page=f"{OC}/bbmp-work-orders-and-payments-2024-25",
         url=f"{OC}/4e539082-aca3-4df0-b676-dc1655cf17d2/resource/67637545-30aa-4a6c-80fa-b46bc22bdc24/download/5cfd015d-cc0b-4526-b6e2-e547e740345a.csv"),
    dict(file="wop_2024_25_225.csv", kind="oc_wodetails", regime="225",
         name="BBMP Work Orders and Payments 2024-25 (225-ward regime)",
         page=f"{OC}/bbmp-work-orders-and-payments-2024-25",
         url=f"{OC}/4e539082-aca3-4df0-b676-dc1655cf17d2/resource/f0606cb1-51f7-48f8-8c38-8995f790a958/download/c57cb808-16cc-4dc7-87e1-4ec55ae58dd3.csv"),
    dict(file="wop_2024_25_243.csv", kind="oc_wodetails", regime="243",
         name="BBMP Work Orders and Payments 2024-25 (243-ward regime)",
         page=f"{OC}/bbmp-work-orders-and-payments-2024-25",
         url=f"{OC}/4e539082-aca3-4df0-b676-dc1655cf17d2/resource/83d27234-f87c-4a0b-a668-a58bd57b5034/download/7b43174d-fa07-46f5-b27b-e7b6ccb1a4bd.csv"),
    dict(file="wop_2025_26_198.csv", kind="oc_wodetails", regime="198",
         name="BBMP Work Orders and Payments 2025-26 (198-ward regime)",
         page=f"{OC}/bbmp-work-orders-and-payments-2025-26",
         url=f"{OC}/280a1196-074e-43ac-acd9-914918b429f5/resource/7a6907c2-6efd-43d6-99ac-1d10f67b3f85/download/bbmp-2025-26-198-wards-work-orders.csv"),
    dict(file="wop_2025_26_225.csv", kind="oc_wodetails", regime="225",
         name="BBMP Work Orders and Payments 2025-26 (225-ward regime)",
         page=f"{OC}/bbmp-work-orders-and-payments-2025-26",
         url=f"{OC}/280a1196-074e-43ac-acd9-914918b429f5/resource/3e784947-b321-4cba-bedb-69de53450a83/download/bbmp-2025-26-225-wards-work-orders.csv"),
    dict(file="wop_2025_26_243.csv", kind="oc_wodetails", regime="243",
         name="BBMP Work Orders and Payments 2025-26 (243-ward regime)",
         page=f"{OC}/bbmp-work-orders-and-payments-2025-26",
         url=f"{OC}/280a1196-074e-43ac-acd9-914918b429f5/resource/6c2e5dfa-845c-440d-808c-3ed45b083059/download/bbmp-2025-26-243-wards-work-orders.csv"),
    dict(file="wop_2025_26_common.csv", kind="oc_wodetails", regime="dept",
         name="BBMP Work Orders and Payments 2025-26 (city-wide departments)",
         page=f"{OC}/bbmp-work-orders-and-payments-2025-26",
         url=f"{OC}/280a1196-074e-43ac-acd9-914918b429f5/resource/71b93422-3d64-4de6-a823-468517f441e3/download/bbmp-2025-26-common-wards-work-orders.csv"),
]

# Legacy 2010-2019 registers from the "BBMP Work Orders and Bill Payment" dataset.
# Resolved at fetch time from the CKAN API (names are stable); see fetch_opencity.py.
LEGACY_DATASET = "bbmp-work-orders-and-bill-payment"
LEGACY_NAME_FILTER = ("303-", "304-", "307-", "309-", "Zone-6", "Nagarothana", "Bill Register", "Job Codes")

# Ward numbers that denote Koramangala under each delimitation regime.
from ingest.area import AREA as _AREA        # noqa: E402  (ward numbers live in config/areas/<slug>.json)

KORAMANGALA_WARDS = _AREA.wards
