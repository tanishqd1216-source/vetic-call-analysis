# AI Calling Agent — Stress Test Query Set (100 Queries)

**Purpose:** Simulated customer calls, built from the support taxonomy (category → subcategory → owning department), for testing whether the AI calling agent correctly identifies intent, classifies the issue, and provides (or routes to) the right resolution.

**How to use:** Feed each "Caller says" line to the agent as if spoken on a call. Compare the agent's classification and resolution against the "Expected" columns. The last section (#91–100) contains harder edge cases — ambiguous, multi-issue, angry-customer, and out-of-scope calls — to test robustness beyond clean single-intent queries.

| # | Caller says (simulated speech) | Category | Subcategory | Department |
|---|---|---|---|---|
| 1 | "The groomer gave my dog a really uneven haircut, one side is way shorter than the other." | Grooming Related Issue | Uneven Haircut | Clinic |
| 2 | "My cat got hurt during the grooming session yesterday, there's a small cut on her paw." | Grooming Related Issue | Injury during service | Clinic |
| 3 | "They didn't finish the full grooming package I paid for, only did a bath." | Grooming Related Issue | Incomplete Service | Clinic |
| 4 | "My dog broke out in a rash after his grooming appointment, is that normal?" | Grooming Related Issue | Allergy post service | Clinic |
| 5 | "Honestly the whole grooming experience was just bad, I'm not happy with any of it." | Grooming Related Issue | Unsatisfactory Service | Clinic |
| 6 | "The groomer didn't follow the instructions I gave, I asked for a trim not a full shave." | Grooming Related Issue | Parent instructions not followed | Clinic |
| 7 | "The staff handled my pet really roughly during the grooming, he was scared the whole time." | Grooming Related Issue | Pet handling (grooming) | Clinic |
| 8 | "I was told my pet gets a medicated bath included but that wasn't done." | Grooming Related Issue | Service inclusions: Hygiene/Medicated Bath | Clinic |
| 9 | "My pet got too anxious mid-session so they stopped the grooming halfway." | Grooming Related Issue | Service Stopped (Anxious Pet) | Clinic |
| 10 | "The vet said my pet needed grooming after the checkup but nobody actually did the post-grooming." | Post Grooming Not given | Doctor was busy, Pet Parent didn't want to wait | Clinic |
| 11 | "I had a valid appointment but the clinic only gave partial grooming service, not what I booked." | Partial Service given Grooming | Correct appointment but Clinic suggested for partial service | Clinic |
| 12 | "The doctor's treatment for my dog's ear infection doesn't seem right, he's worse now." | OPD consultation Issue | Inapt Treatment | Medex |
| 13 | "The vet barely explained anything during the consult, I still don't understand what's wrong." | OPD consultation Issue | Communication lapse | Medex |
| 14 | "My pet is in really critical condition right now and I need urgent help." | OPD consultation Issue | Critical Pet | Medex |
| 15 | "The prescription they gave me has the wrong dosage written on it." | OPD consultation Issue | Incorrect EMR/Prescription | Medex |
| 16 | "Nobody handled my puppy properly during the OPD visit, he was struggling and no one helped." | OPD consultation Issue | Pet Handling (in OPD) | Medex |
| 17 | "The doctor never filled in the medical record after my pet's visit." | OPD consultation Issue | EMR not filled by Dr. | Medex |
| 18 | "I want to know exactly what treatment and vaccination was given, the notes aren't clear." | OPD consultation Issue | EMR/Service/treatment/vaccination/Dressing Clarification | Medex |
| 19 | "I keep getting calls from your sales team, like five times a day, please stop." | Appointment_Process | Too Many Calls/messages | Sales |
| 20 | "Nobody has gotten back to me after I requested an appointment two days ago." | Appointment_Process | No Response from Vetic team | Sales |
| 21 | "The appointment got booked wrong, wrong date and time altogether." | Appointment_Process | Incorrect/Missing appointment | Sales |
| 22 | "I was quoted one price on the call but charged something completely different." | Appointment_Process | Incorrect price/information provided | Sales |
| 23 | "The person on the phone was quite rude while booking my appointment." | Appointment_Process | Staff Behaviour | Sales |
| 24 | "I have a general question about how the appointment process works." | Appointment_Process | Others | Clinic |
| 25 | "The consultation prices seem way too high compared to before." | Appointment_Process | Prices too high | Ops |
| 26 | "The waiting time at the clinic today was almost two hours, that's unacceptable." | In-Clinic issue | High Waiting Time | Clinic |
| 27 | "One of the receptionists at the clinic was really impolite to me today." | In-Clinic issue | Staff Behaviour | Clinic |
| 28 | "I got to the clinic and the doctor I booked wasn't even available." | In-Clinic issue | Provider not available | Clinic |
| 29 | "The clinic didn't tell me all the details about the procedure before starting it." | In-Clinic issue | Complete information not provided prior to service | Clinic |
| 30 | "My pet was handled very carelessly by the staff inside the clinic." | In-Clinic issue | Pet handling | Clinic |
| 31 | "The clinic floor and rooms honestly looked pretty unhygienic today." | In-Clinic issue | Unhygienic Clinic | Clinic |
| 32 | "They kept trying to upsell me extra food and packages I never asked for." | In-Clinic issue | Overselling packages/food/others | Clinic |
| 33 | "I got billed but nobody explained what each charge on the bill actually was." | In-Clinic issue | Bill Not explained | Clinic |
| 34 | "The app keeps crashing every time I try to open my pet's profile." | Tech/VetBuddy | App Related | Tech |
| 35 | "The EMR updates from my last vet visit still haven't shown up in the app." | Tech/VetBuddy | EMR updates not received | Tech |
| 36 | "I'm having some other technical issue with VetBuddy I can't quite explain." | Tech/VetBuddy | Others | Tech |
| 37 | "I want to check how much of my membership benefits I've already used." | Membership Query | Utilisation related | Tech |
| 38 | "The delivery app says my order was delivered but I never got the package." | Home_Delivery | Marked Delivered but not Delivered | Home Delivery |
| 39 | "The delivery guy was rude and impatient when he dropped off my order." | Home_Delivery | Rider Behaviour Issue | Home Delivery |
| 40 | "I ordered dog food but they sent me an entirely different product." | Home_Delivery | Wrong Product Delivered | Home Delivery |
| 41 | "There's a mismatch, the app shows a different quantity than what I actually received." | Home_Delivery | Inventory Mismatch/Wrong Product | Home Delivery |
| 42 | "The rider went to the wrong address, not the one I put in the app." | Home_Delivery | Address mismatch | Home Delivery |
| 43 | "The seal on the medicine box I received was already broken." | Home_Delivery | Quality Issue/Damaged/Broken seal Product | Home Delivery |
| 44 | "The delivery rider asked me to pay extra cash on top of the order amount." | Home_Delivery | Extra Charge by Rider | Home Delivery |
| 45 | "The OTP for my delivery never came through on my phone." | Home_Delivery | OTP issue | Home Delivery |
| 46 | "I only received part of the sub-order, the rest is just missing with no explanation." | Home_Delivery | Partial Order Query for Sub orders | Home Delivery |
| 47 | "The quantity I received was less than what I actually ordered." | Home_Delivery | Less Quantity Delivered | Home Delivery |
| 48 | "There were way too many calls and no clear updates through this whole delivery process." | Home_Delivery | Delivery process not smooth (Too many calls/lack of intimation) | Home Delivery |
| 49 | "I was supposed to get a free gift with my order and it never arrived." | Home_Delivery | Free Gift Issue Clinic Issue | Home Delivery |
| 50 | "My delivery is way outside the promised time window now." | Home_Delivery | Delay Delivery - Outside TAT (RFP Delay) | Home Delivery |
| 51 | "The packaging on my order arrived completely torn and damaged." | Home_Delivery | Packaging Issue | Home Delivery |
| 52 | "The product I received has already expired, the date on it has passed." | Home_Delivery | Expired Product Delivered | Home Delivery |
| 53 | "The invoice you sent me has an incorrect amount billed." | Invoice Related Query | Incorrect Billing | Clinic |
| 54 | "I need someone to clarify a few line items on my invoice, I don't follow it." | Invoice Related Query | Invoice Clarification required | Tech |
| 55 | "I was billed for a product that was never actually handed over to me." | Invoice Related Query | Product billed not handed over | Clinic |
| 56 | "My membership discount wasn't applied even though it should have been." | Invoice Related Query | Membership Discount not applied | Tech |
| 57 | "The dog food I want to buy seems overpriced right now, why the increase?" | MedEx/Food/Acc_Category | Prices too high | Ops |
| 58 | "The accessory I received doesn't feel like the quality I paid for." | MedEx/Food/Acc_Category | Quality Issue | Longtail |
| 59 | "The item I want to order shows as not available at all." | MedEx/Food/Acc_Category | Product not available | Longtail |
| 60 | "I had a tele consultation booked and the vet never joined the call." | Tele Consultation | Tele Missed by PP | Tele |
| 61 | "My scheduled video consultation was missed from the clinic's side." | Tele Consultation | Tele Missed by Clinic | Tele |
| 62 | "My lab report is way past the promised turnaround time from an outside lab." | Lab Related query | lab report delay outside TAT - external | Ops |
| 63 | "I think there's an error in the report the external lab sent back." | Lab Related query | Incorrect report - External Lab | Ops |
| 64 | "I need help understanding the X-ray results from my pet's scan." | Lab Related query | USG / X Ray interpretation | Medex |
| 65 | "The lab told me they need to resample, and I've agreed to it, what's next?" | Lab Related query | Resampling required - Accepted | Ops |
| 66 | "The lab is asking to resample but I don't want to, I'm declining that." | Lab Related query | Resampling required - Denied | Ops |
| 67 | "My internal lab report is delayed even beyond the usual turnaround time." | Lab Related query | lab report delay outside TAT - internal | Ops |
| 68 | "My medicine order is stuck, seems like it's an inventory problem on your end." | Medicine fulfillment issue | Issue at inventory end | Longtail |
| 69 | "The clinic seems to be the reason my medicine fulfillment is stuck." | Medicine fulfillment issue | Issue at clinic end | Longtail |
| 70 | "My medicine order was supposed to come via home delivery but it's stuck there." | Medicine fulfillment issue | Issue at HD end | Longtail |
| 71 | "The pet food order I placed hasn't moved, sounds like an inventory issue." | Food fulfillment issue | Issue at inventory end | Longtail |
| 72 | "The food order fulfillment seems delayed because of something on the clinic's side." | Food fulfillment issue | Issue at clinic end | Longtail |
| 73 | "I want a refund on my membership, I don't want to continue it anymore." | Membership Query | Membership Refund | Clinic |
| 74 | "I feel like I was sold this membership under false promises, that's not okay." | Membership Query | Membership wrong selling | Clinic |
| 75 | "The membership invoice I got doesn't add up correctly." | Membership Query | Membership invoice related | Tech |
| 76 | "Something's wrong, the app shows my membership utilisation incorrectly." | Membership Query | Wrong Utilization _Tech Issue | Tech |
| 77 | "I was given wrong information about what my membership actually covers." | Membership Query | Incorrect information shared - Membership | Clinic |
| 78 | "I had a discount code that just didn't apply on my order at all." | App Related Query | Offer/Discount not applied | Tech |
| 79 | "I can't see my active membership anywhere in the app." | App Related Query | Membership not visible | Tech |
| 80 | "My latest invoice isn't showing up in the app at all." | App Related Query | Invoice not visible | Tech |
| 81 | "I can't find the medical record for my pet's last visit in the app." | App Related Query | EMR not visible | Tech |
| 82 | "The appointment showing in the app is either missing or just wrong." | App Related Query | Missing/Incorrect appointment | Tech |
| 83 | "I'm not getting the OTP to log into the app, tried several times now." | App Related Query | Login/OTP Issue | Tech |
| 84 | "The app keeps sending me the same notification reminder over and over." | App Related Query | Notification Reminder | Tech |
| 85 | "I paid an advance for a service and now I need that refunded." | Refund Related Query | Refund against advance | Finance |
| 86 | "My pet passed away recently and I need a refund processed for that reason." | Refund Related Query | Refund - Pet Deceased | Finance |
| 87 | "I'd like a refund on a service I paid for but never actually received." | Refund Related Query | Service / Product Refund | Finance |
| 88 | "I think I got charged twice for the same payment, can you check?" | Payment Related issue | Payment done multiple times | Finance |
| 89 | "I paid already but the app still isn't reflecting that payment." | Payment Related issue | Client paid not reflecting | Finance |
| 90 | "There was some issue with the cash-on-delivery payment for my order." | Home_Delivery | COD Payment Issue | Home Delivery |
| 91 | "I want to return a medicine I ordered from the pharmacy, wrong item entirely." | E-Pharmacy | Medicine Return | E-Pharmacy |
| 92 | "The medicine I got through e-pharmacy arrived damaged and honestly looks wrong too." | E-Pharmacy | Expired / Damage /Wrong Medicine Received | E-Pharmacy |
| 93 | "App shows my pharmacy order as delivered but nothing showed up at my door." | E-Pharmacy | Marked Delivered not delivered | E-Pharmacy |
| 94 | "My e-pharmacy order is delayed well beyond what was promised." | E-Pharmacy | Delay Delivery | E-Pharmacy |
| 95 | "I received fewer tablets than what I actually ordered from the pharmacy." | E-Pharmacy | Less Qty Received | E-Pharmacy |
| 96 | *[Multi-intent]* "Two things — my delivery never arrived AND I was double charged for it, can you sort both?" | Home_Delivery / Payment Related issue | Marked Delivered but not Delivered / Payment done multiple times | Home Delivery / Finance |
| 97 | *[Ambiguous/vague]* "Something's just off with my order, I don't know what exactly, can someone check?" | Home_Delivery | Others | Home Delivery |
| 98 | *[Angry escalation]* "This is the third time I'm calling about the same rude staff member, I want a manager NOW." | In-Clinic issue | Staff Behaviour | Clinic |
| 99 | *[Out-of-scope]* "Hey, unrelated question, but do you know a good all-around vet clinic near Gurugram for a second opinion?" | Out of scope | N/A — general inquiry, not in taxonomy | N/A |
| 100 | *[Silence/unclear speech]* "...hello? Can you— sorry, bad network, I was saying something about my— [call drops]" | Unclear/Dropped call | N/A — needs callback/clarification flow | N/A |

## Notes on edge cases (#96–100)
These five are deliberately outside the "clean single-intent" pattern to see how the agent degrades:
- **#96** tests whether the agent can split a query into two distinct tickets/departments instead of only capturing one.
- **#97** tests whether the agent asks a clarifying follow-up question rather than guessing a category.
- **#98** tests de-escalation handling and correct escalation routing under an angry tone.
- **#99** tests whether the agent correctly declines/redirects a question outside its knowledge base instead of hallucinating a clinic recommendation.
- **#100** tests the agent's dropped-call/callback recovery flow.
