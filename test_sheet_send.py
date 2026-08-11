from services.google_sheets import get_rows
from services.whatsapp import send_template


rows = get_rows()

for row in rows:
    phone = str(row["phone"]).strip()

    response = send_template(
        phone,
        "timmins_software_testing_intro",
        language_code="en",
        variables=[str(row.get("name", "")).strip() or "there"],
    )

    print(phone)
    print(response.status_code)
    print(response.text)

    break
