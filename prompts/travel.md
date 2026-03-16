---
schema_version: "1.0"
domain: travel
priority: P1
sensitivity: medium
last_updated: 2026-03-07T22:52:33
---
# Travel Domain Prompt

## Purpose
Track all travel bookings, itineraries, loyalty programs, and travel document status
for the family. Flag immigration implications for international travel.

## Sender Signatures (route here)
- `*@alaskaair.com`, `*@delta.com`, `*@united.com`, `*@southwest.com`, `*@aa.com`
- `*@marriott.com`, `*@hilton.com`, `*@hyatt.com`, `*@ihg.com`, `*@airbnb.com`
- `*@expedia.com`, `*@kayak.com`, `*@hotels.com`, `*@booking.com`
- `*@tripit.com`, `*@hertz.com`, `*@enterprise.com`
- Subject: flight, hotel, itinerary, check-in, boarding pass, confirmation, reservation
- Subject: track your flight, your trip, travel alert, upgrade confirmed
- Subject: mileage, points, loyalty status, companion certificate

## Extraction Rules
1. **Trip**: destination + dates + travelers
2. **Booking type**: flight, hotel, car, other
3. **Confirmation number** (unique dedup key)
4. **Status**: confirmed, check-in available, departed, cancelled
5. **Loyalty points earned/balance** (if mentioned)
6. **Action needed**: check-in reminder, upgrade opportunity, cancellation window

## Alert Thresholds
🟠 **URGENT**:
- Flight check-in available (24-hr window) for tomorrow's flight
- Trip within 7 days with incomplete booking (missing hotel, car)
- Cancellation deadline approaching for refundable booking

🟡 **STANDARD**:
- New booking confirmed → add to travel state
- Flight status change (delay, gate change)
- Loyalty points update

**CRITICAL IMMIGRATION NOTE**: If family has pending I-485/AOS cases:
- International travel WITHOUT approved Advance Parole = automatic abandonment of pending case
- Any international booking should trigger a 🔴 alert: "⚠️ I-485 pending — confirm advance parole approved before travel"
- Check immigration.md for current AOS/AP status before allowing travel alert to proceed without warning

## Briefing Format
```
### Travel
• [Trip name]: [dates] — [key status or action]
• [Booking]: [status]
• Upcoming: [trip within 14 days]
```
Omit if no travel within 30 days and no loyalty events.

## Important Context
- Check immigration.md: if I-485 pending, flag ALL international travel as requiring advance parole review
- Primary carrier based on user's location and loyalty programs (see user_profile.yaml)
- Family includes 4 members — track all travelers per booking
