Good. This file is enough to start. Your build should be **Excel/Sheet → WhatsApp warm outreach → reply capture → qualification → update lead status**.

## 1. What your current lead file gives you

Useful columns:

```text
id
created_time
campaign_name
form_name
platform
who_will_pay?
full_name
email
phone
job_title
company_name
lead_status
```

For WhatsApp outreach, the most important ones are:

```text
full_name
phone
email
campaign_name
form_name
who_will_pay?
job_title
company_name
lead_status
```

## 2. Clean the sheet first

Before sending any WhatsApp messages, create a cleaned working sheet.

Keep original file untouched.

Create a new sheet/table called:

```text
whatsapp_outreach_leads
```

Use these columns:

```text
lead_id
created_time
full_name
phone
email
campaign_name
form_name
platform
who_will_pay
job_title
company_name
lead_status

whatsapp_status
template_sent_at
last_reply
last_reply_at
bot_stage
qualification_status
assigned_to
notes
```

## 3. Add WhatsApp automation statuses

Use these values only:

```text
new
ready_to_send
template_sent
replied
qualifying
qualified
not_interested
invalid_number
failed
human_handoff
```

Example:

| whatsapp_status  | Meaning                        |
| ---------------- | ------------------------------ |
| `new`            | Imported but not checked       |
| `ready_to_send`  | Valid phone, can send outreach |
| `template_sent`  | First WhatsApp message sent    |
| `replied`        | Lead replied                   |
| `qualifying`     | Bot is asking questions        |
| `qualified`      | Good lead                      |
| `not_interested` | User opted out                 |
| `invalid_number` | Bad phone                      |
| `failed`         | WhatsApp send failed           |
| `human_handoff`  | Sent to sales/person           |

## 4. Outreach flow

This is the actual flow:

```text
Excel/Sheet lead
        ↓
Clean phone number
        ↓
Check lead_status
        ↓
Send approved WhatsApp template
        ↓
Update whatsapp_status = template_sent
        ↓
Wait for reply through webhook
        ↓
Ask qualification questions
        ↓
Update answers in sheet
        ↓
Score/route lead
        ↓
Notify human
```

## 5. First template message

Because this is business-initiated WhatsApp outreach, first message should be an approved template.

Template purpose:

```text
Follow up with people who submitted the July 2026 intake lead form.
```

Template content:

```text
Hi {{1}}, this is Timmins Training Consulting.

Thanks for your interest in our July 2026 intake.

Would you like us to help you with the next steps?
```

Buttons:

```text
Yes, send details
Book a callback
Not interested
```

Variables:

```text
{{1}} = full_name
```

Template name:

```text
july_2026_intake_followup
```

Category:

```text
Marketing
```

## 6. Bot flow after they reply

If user clicks **Yes, send details**:

```text
Great. Are you enquiring for yourself or someone else?

1. Myself
2. My company/team
3. Someone else
```

Then:

```text
What are you mainly interested in?

1. Training programme details
2. Fees/payment options
3. Course schedule
4. Company/team training
5. Speak to advisor
```

Then:

```text
When would you like our team to contact you?

1. Today
2. Tomorrow
3. This week
4. I only want details on WhatsApp
```

If user clicks **Book a callback**:

```text
Sure. Please share your preferred callback time.
```

If user clicks **Not interested**:

```text
No problem. We won’t follow up further. Thank you.
```

Update:

```text
whatsapp_status = not_interested
```

## 7. Routing logic

Use your columns like this:

```text
who_will_pay? = company/employer
→ route to B2B/corporate sales

job_title exists + company_name exists
→ possible corporate lead

who_will_pay? = self
→ route to admissions/sales

Book a callback
→ human_handoff

Asks fees/payment
→ sales/admissions

Not interested
→ stop
```

## 8. Lead score

Simple score:

```text
Has phone = +20
Has company_name = +15
Has job_title = +10
who_will_pay = company/employer = +25
Clicked callback = +30
Asked fees/details = +20
Responded within 24h = +10
```

Score meaning:

```text
70+ = hot
40-69 = warm
below 40 = nurture
```

## 9. What we build first

Build in this exact order:

```text
1. Read Excel/Google Sheet leads
2. Normalize phone numbers
3. Mark valid leads as ready_to_send
4. Send WhatsApp template to ready_to_send leads
5. Update sheet after send
6. Create webhook to receive replies
7. Update sheet with replies
8. Continue bot questions
9. Route qualified leads
```

## 10. Your MVP architecture

```text
Google Sheet / Excel
        ↓
Python backend
        ↓
Meta WhatsApp Cloud API
        ↓
Lead replies
        ↓
Webhook
        ↓
Google Sheet update
        ↓
Sales notification
```

## 11. Before coding, confirm these 5 things


