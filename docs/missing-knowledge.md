# What the clinic knows and hasn't told us

**This is the list you hand an owner on day one.**

Every item here is something a customer actually asked and the bot could not
answer — not because retrieval failed, not because the model was wrong, but
because nobody ever put the fact in. Three separate problems this session traced
back to exactly this, from three different directions:

- **`syn-which-room`** — "which room does the doctor sit in?" No fact holds a
  room number. Measured and not fixable at retrieval or by a prompt rule; the
  only real fix is the data.
- **The payment / certificates cluster** — payment methods, form 086, sanepid
  certification, spravka. Eleven `gap` test cases exist purely because the
  clinic never stated any of it.
- **The completeness problem** — extraction review shows the owner what WAS
  proposed and can never surface what was never proposed, so an omitted fact is
  invisible. Recorded under *Open* in `docs/design-decisions.md`.

Gap counts below come from `gaps.jsonl` (310 entries at time of writing) and
from the `expect="gap"` cases in `questions.py`. A question appearing here is
**not** a bug report — the refusal is correct behaviour. It is a sales list.

---

## Asked repeatedly, and answerable if someone tells us

Ordered by how often it was actually asked.

| What the customer wants | Example asked | Why we can't answer |
|---|---|---|
| **Closing time, plainly stated** | "Nechida yopilasila?" / "Nechida yopilasiz?" | Hours exist as a range; the terse "what time do you shut" phrasing keeps missing |
| **MRT — offered at all, and price** | "Сколько стоит МРТ?", "MRT qilasizmi?" | No MRT service fact of any kind |
| **Payment methods** | "Вы принимаете карту Humo?", "Nalichka to'lasa bo'ladimi?" | No payment fact. Uzcard/Humo/cash/transfer are all unknown to us |
| **Online booking** | "Onlayn navbatga yozilsa boʻladimi?" | No booking procedure exists as a fact |
| **Rescheduling an existing appointment** | "Ertaga palonchi doxtorga yoziludim, vaxtini sal keginroqa surib bo'ladimi?" | No booking or rescheduling data |
| **LOR (ENT) — on staff at all, and Saturday hours** | "Jonsarak aka (LOR) qachon keladila? Shanbayam ishlidilarmi?" | No LOR on staff. If there is one, we don't know |
| **Ophthalmologist** | "Oculist qachon rabochiy den?" | No ophthalmologist fact |
| **Preparation instructions before tests** | "Mrt ga tushishdan oldin choy poy ichsa buraveradimi yoki och qoringami?" | No fasting/preparation facts. The single most tempting thing to infer from general medical knowledge, and the bot correctly refuses |
| **Room numbers per doctor** | "Vrach qatda o'tiradi?" | No room fact |
| **Appointment availability / queue length** | "Kardiologga ochered bormi xozir?", "Ertaga vrachda bo'sh vaqt bormi?" | Real-time state we cannot hold. **Probably not answerable even with data** — see below |
| **Certificates: spravka, form 086** | "Sizlarda spravka 086 beriladimi?" | No certificate service fact |
| **Sanitary / licensing certification** | "Klinikayla sanepidda tekshiruvdan o'tganmi?" | No compliance fact |
| **Inpatient beds** | "Krovotga yotish narxi nech pul?" | No inpatient service fact |
| **Blood-pressure measurement price** | "Davlenniya o'lchash nech pul?" | No fact. May well be free — but we must not guess that |
| **Kidney ultrasound** | "Pochka UZI qilish kerek edi." | Abdominal and breast UZI exist; kidney does not |
| **Injections / procedure room** | "Ukól qiladigan feldsher bormi?" | No nursing or procedure-room fact |
| **Prescriptions at consultation** | "Dori yozib beradimi konsultatsiyada?" | No prescription policy fact |
| **Discounts / promotions** | "MRTga skidka bormi xozir?" | No discount fact |
| **Chief doctor** | "Glavniy vrach priyomiga qanaqa yozilsa bo'ladi?" | No chief-doctor fact |
| **Lunch break** | "Klinika obetda ishliydimi?" | Hours exist; whether there is a break does not |
| **Imaging turnaround** | "Fluorografiya otveti qachon chiqadi?" | Blood-test turnaround exists; imaging does not |
| **Insurance / treatment coverage** | "Kandisiyami nma balosi boru, ushanga to'lasa bo'ladimi silada?" | No coverage or condition-treatment facts |

## Worth asking the owner for even though nobody asked yet

- **Emergency guidance.** Currently hardcoded to 103 / 112 as a deliberate,
  verified exception. If the clinic has its own out-of-hours instruction, storing
  it as a fact makes it *retrieved* rather than asserted and closes the
  exception. See the triage section in `docs/design-decisions.md`.
- **Holidays.** The date lines resolve today and tomorrow, but nothing knows
  about a public holiday falling on either. Recorded as a limitation on
  `temporal-next-tuesday`.
- **A confirmed neurologist price** — still sitting unconfirmed in the review
  queue.

## Asked, but probably NOT solvable by adding a fact

Listed so nobody promises them to an owner:

- **Live queue length and same-day slot availability.** These are real-time
  state, not knowledge. Answering them needs an integration with whatever system
  actually holds the schedule, not a row in `fact`.
- **Symptom → specialty routing.** "Which doctor should I see for this?" is
  medical advice however it is worded, and is **permanently out of scope**. If
  the clinic wants to publish its own triage guidance, that is theirs to state
  and can be stored as facts — it is not ours to derive.

---

## How to use this

Facts here are typed straight in and land confirmed on write (`source_id IS
NULL`). Prices go in as **approximate ranges**, never exact figures. Anything
extracted from a document instead stays unconfirmed until reviewed.

Rebuild the evidence with:

```
python -c "import json,collections; rows=[json.loads(l) for l in open('gaps.jsonl',encoding='utf-8') if l.strip()]; print(collections.Counter(r['question'] for r in rows).most_common(40))"
```
