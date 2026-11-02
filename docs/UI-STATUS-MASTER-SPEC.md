# Universe Agent — المواصفة الماستر لإصلاح الواجهة وتحديثات الحالة

> ملف قرار/مراجعة. اقرأه بندًا بندًا، وعلّق برأيك: ✔️ موافق / ✖️ احذف / 🔄 غيّر إلى كذا.
> كل بند مرقّم برقم مرجعي (مثل `A3`) ليسهل التعليق عليه.
> الحالة: **مسودة للمراجعة — لم ينفَّذ شيء بعد.**
> هذا الملف يخلف `UI-OVERHAUL.md` v2 (المنفَّذ سابقًا) ويغطي الجيل التالي من أسطح الواجهة فقط.

---

## 0. المبادئ الحاكمة (Non-negotiables)

| # | المبدأ |
|---|--------|
| 0.1 | **صفر نوافذ منبثقة داخل الـchat. نهائيًا.** لا modal فوق المحادثة، لا scrim، لا "بطاقة منبثقة" عائمة. الاستثناء الوحيد المسموح: `ConfirmDialog` لأفعال هدامة صريحة (حذف chat/project) في Sidebar وSettings فقط — لأنه لا يوجد "chat" أصلاً في ذلك السياق. |
| 0.2 | كل تحديثات الحالة تعيش في مكانين فقط: **شريط الحالة العلوي** (فوق الـchat مباشرة) + **الشريط الجانبي الأيمن** (Right Rail). |
| 0.3 | المعاينة (ملفات، مواقع، فيديو، متصفح حي) تفتح **داخل الـRight Rail** كـ"سطح معاينة موحّد"، لا كنافذة. |
| 0.4 | مجرة واحدة فقط في الواجهة كلها: **UniverseOrb** الثابتة (تفاصيلها في §C). تُحذف ThinkingSpark وMiniUniverse بالكامل. لا يتغير لونها ولا شكلها — هي ثابتة كما طلبت. |
| 0.5 | أي نتيجة ينتجها agent (ملف، موقع، صورة، فيديو) تظهر **كرمز-سطر مضمّن (chip) في تدفق الـchat نفسه** (سطر واحد، ليس نافذة)، الضغط عليه ينقل الـRail للمعاينة. |
| 0.6 | التصميم الداكن الحالي (Linear × Perplexity، توكنات `--ds-*`) يبقى الأساس؛ اللمسات تُقاس على Apple HIG وLinear. |

---

## A. تشريح الشاشة الجديد (Layout)

```
┌──────────┬──────────────────────────────┬─────────────────────┐
│ Sidebar  │  Topbar (موجود، بلا تغيير)   │  Right Rail (جديد)  │
│ (موجود)  ├──────────────────────────────┤  ─ حالة + معاينة ─  │
│          │  ┌ StatusStrip (جديد) ─────┐ │                     │
│          │  │ 🌀UniverseOrb  النص الحي  │ │  Tabs:              │
│          │  └─────────────────────────┘ │  Activity | Preview │
│          │  Chat column (رسائل نظيفة)   │  Browser | Files    │
│          │  + inline chips للمخرجات     │                     │
│          ├──────────────────────────────┤ (عرض قابل للسحب     │
│          │  Composer (+ banner موافقة)  │  320→560px)         │
└──────────┴──────────────────────────────┴─────────────────────┘
```

- **A1 — StatusStrip**: شريط بارتفاع ~40px أعلى عمود الـchat مباشرة (تحت Topbar). يحتوي يسارًا UniverseOrb (~28px) ثم سطر نص حي واحد ("ما الذي يفعله universe الآن"). يظهر فقط أثناء مهمة جارية، ويتلاشى خلال 150ms عند انتهائها (بعدها تظهر خلاصة سطر واحد داخل الرسالة — C4/D). **لا يزداد ارتفاعه أبدًا**؛ أي تفصيل إضافي يذهب للـRail.
- **A2 — Right Rail**: لوحة يمين بعرض 360px افتراضيًا، قابلة للسحب، قابلة للطي بالكامل. تحل محل `AgentsPanel` الحالي (تُعاد كتابتها) وتضيف أسطح المعاينة. تبويباتها الأربعة في §F–§H.
- **A3 — Chat column**: يصبح **محادثة فقط**: فقاعات user، وردود agent نهائية بـmarkdown (جديد: renderer كامل بدل `pre-wrap`)، وأسطر chips للمخرجات. يُحذف منه ActivityFeed القديم بكل عناصره (StatusLine، PlanList، ToolCards، HandoffLine، SkillChips، Recap) — ينتقل كله إلى Rail. الـ`ConsentModal` العام يُحذف (E2).
- **A4 — isEmpty state**: شاشة الترحيب الصفرية الحالية تبقى كما هي.
- **A5 — OfflineBanner**: يبقى (شريط علوي رفيع)، يندمج بصريًا داخل StatusStrip إن وُجد شريط نشط.
- **A6 — فتح Rail تلقائيًا**: بنفس منطق `AgentsPanel` الحالي: يفتح عند أول حدث يستدعي agents، والإغلاق اليدوي لا يعيده حتى مهمة جديدة.

---

## B. قائمة المحذوفات (Deletion list)

| # | العنصر اليوم | البديل |
|---|--------------|--------|
| B1 | `ThinkingSpark.tsx` (44px + bars + "thinking"/"waiting for universe") | UniverseOrb في StatusStrip (§C) |
| B2 | `ConsentModal.tsx` (modal عام بـscrim فوق الواجهة كلها) | ConsentBanner مضمّن فوق الـcomposer مباشرة (E2) — بلا scrim، بلا حجب المحادثة |
| B3 | `MiniUniverse.tsx` (كرة 220×140 داخل بطاقة الـagent + flares + "Worked for Xs") | تُحذف. نبضات الأحداث تُغذّي UniverseOrb، وخلاصة العمل تصبح سطرًا نصيًا داخل الرسالة |
| B4 | `ActivityFeed.tsx` بكل عناصره داخل الرسالة | يعاد توجيهه بالكامل إلى تبويب Activity في Rail (§F)؛ ProviderHalt يظهر في StatusStrip + الرسالة الختامية |
| B5 | `FilesPanel.tsx` (كود ميت أصلًا — لا أحد يستورده) | تبويب Files في Rail (H4) |
| B6 | الـ`ConsentBanner` المكرر داخل ActivityFeed (مسار مزدوج مع B2) | يبقى مسار واحد فقط: banner فوق الـcomposer |

> **B7 — سؤال مفتوح**: مجرة الذاكرة الكاملة (memory sphere كبيرة) لم يعد لها موضع بعد حذف MiniUniverse. خياراتك: (أ) خلفية خافتة جدًا داخل تبويب Activity، (ب) حذفها من الواجهة كليًا (تبقى للـWelcome مستقبلًا)، (ج) زر في Topbar يفتحها كسطح كامل فوق مساحة الـchat (تبديل عرض، ليس modal). **توصيتي: (ب) الآن و(ج) لاحقًا.**

---

## C. UniverseOrb — مجرة التفكير الثابتة (القطعة التي طلبتها بحرفية)

- **C1 — الهوية**: كرة مجرية حلزونية 3D **صغيرة جدًا** (~28px في الشريط)، شفافة 100% (بلا خلفية، بلا صندوق، بلا حدود)، تبدأ بالدوران والتفاعلات الضوئية فور الظهور. تُبنى من نفس محرك `UniverseCanvas`/GLSL الحالي بـprofile جديد `orb`: ~140–180 نقطة أذرع + ~40 نواة + bloom هادئ + خيوط plasma نابضة في النواة. **ثابتة الشكل واللون دائمًا** — لا تتغير حسب الـagent، وهي الـthinking loading الدائم والوحيد لـUniverse Agent كما قررت.
- **C2 — السلوك**: سرعة الدوران تتبع معدل الأحداث (نفس معادلة `energy` الحالية: أحداث/ثانية ÷ 8 مع تنعيم): تتسارع عندما يشتغل التنفيذ، تخفت بهدوء أثناء تفكير الـLLM. نبضة ضوء في الأذرع عند كل `tool_finished`. الدوران لا يتوقف إلا عند نهاية المهمة.
- **C3 — النص المرافق**: إلى يمينها سطر واحد يتحدث باستمرار بصيغة مضارع ("universe is planning"، "research — reading 3 sources"، "browser — clicking on page"، "waiting for your approval"). مصدره آخر حدث مؤثر (خريطة كاملة في F). الحركة عند التغير: fade/slide عمودي 120ms.
- **C4 — حالاتها**: `idle` مخفية تمامًا؛ `running` تدور + نص؛ `paused` تبهت 50%؛ `waiting-consent` دوران بطيء + توهج خفيف حولها + "needs your approval"؛ `finished` تتلاشى خلال 600ms ويُكتب سطر الخلاصة داخل الرسالة ("done in 18.4s · 7 tools · code+review").
- **C5 — الوصولية**: `prefers-reduced-motion` → كرة ثابتة + "working…" نصيًا. `aria-live="polite"` على سطر النص (كما هو اليوم).
- **C6 — الأداء**: Orb واحدة فقط في الواجهة كلها (في StatusStrip). لا canvases أخرى تتكرر في Rail — أسطر الـagents هناك نصية بالكامل (قررتَ: Orb واحدة ثابتة، بدون نسخ ملوّنة). سقف GPU محترم: `dpr≤1.25`، `frameloop demand` عند التلاشي، إيقاف كامل بعد النهاية.

---

## D. استدعاء الـagents — كيف يظهر للمستخدم (تبويب Activity في Rail)

```
Session: "إعادة بناء صفحة تسجيل الدخول"
├─ 📋 Plan (6 خطوات)  ← طيّ افتراضي بعد الاكتمال، مفتوح أثناء العمل
│   1. planning ✓   2. code ●   3. review ○   4. security ○
├─ 🟣 planning agent              ✓ 1.2s
├─ 🔵 code agent                  ● working…          [⏹]
│    ├ 🔧 file.read ×3            ✓ ✓ ✓               (أسطر قابلة للطي)
│    ├ 🔧 file.write auth.tsx     ● 1.2s
│    └ ⤷ → review: "ready for security pass…"
├─ 🟠 review agent                ○ waiting
└─ 🌐 browser agent               ● live → [تبويب Browser]
```

- **D1 — سطر الـagent**: glyph + اسم + **لون تمييز هادئ خاص بكل agent** (12 لونًا ثابتة مشتقة من الـpalette — تُستخدم للنص والشارات فقط، وليس للمجرة؛ Orb واحدة ثابتة) + حالة (spinner/✓/✗/waiting) + elapsed حي + زر ⏹ للسطر الجاري (D7).
- **D2 — الأدوات داخل السطر**: كل أداة = سطر فرعي بحالة ملونة (queued رمادي / running ● / ✓ مع duration / ✗ بلون rose). ضغطة على السطر تفرده inline (لا نافذة) ويعرض args_summary ثم detail النتيجة. الأدوات المعلنة مخفية اليوم (skill.search/read) تبقى مخفية.
- **D3 — handoff**: سهم رفيع بين سطرين + "what_was_done" (سطر واحد، expand). مشروط بإصلاح I4 (ملء `to_agent` من الخطة — حاليًا تصل null فلا تُعرض).
- **D4 — llm_round**: لا صفوف متراكمة؛ يظهر فقط كنص C3 ("…thinking").
- **D5 — skill chips**: badge صغير على يمين سطر الـagent ("used: react-perf") + tooltip بالنظام المداري.
- **D6 — memory_updated**: صامت في الواجهة (يبقى يغذي بيانات الذاكرة خلف الكواليس).
- **D7 — تخطي agent**: ⏹ = تخطي هذه الخطوة وتمرير القرار للـorchestrator (E4)، تصبح ✗-skipped في الـPlan.
- **D8 — provider_halt**: شريط أحمر هادئ أعلى تبويب Activity + سبب + زر "Open settings" (inline، لا نافذة).
- **D9 — recap**: عند النهاية يستبدل الـPlan بملخص قابل للطي: agents×، tools×، skills×، handoffs×، duration — نفس أرقام Recap الحالية.

---

## E. التحكم من المستخدم أثناء المهمة (قرارك: steer يمر عبر orchestrator دائمًا)

- **E1 — Stop شامل**: زر الإيقاف الثلاثي في الـcomposer يبقى كما هو.
- **E2 — Consent (بديل الـmodal)**: عند `consent_required` يظهر banner فوق الـcomposer مباشرة (يدفعه لأسفل — لا حجب): درع + `tool` + `agent` + args_summary + عدّاد 60s + Approve/Deny. Esc = Deny. يتلاشى فور القرار. يتوحد المسار المزدوج الحالي (modal + inline) في هذا الواحد.
- **E3 — Steer**: أثناء مهمة جارية، إرسال الـcomposer **لا يفتح sessionًا جديدًا** — يرسل رسالتك إلى **orchestrator فقط**:
  - `POST /api/session/{id}/steer {text}` (جديد).
  - Orchestrator يستلمها عند أقرب نقطة قرار بين خطوات الخطة: يفهم "أين توقفت المهمة، ولماذا يقفها المستخدم، وماذا يريد"، ثم **يعيد توليد بقية الخطة بالكامل** من تلك النقطة (replan). لا تقاطع حاد لأداة نصف منتهية — الأداة الجارية تكمل جولتها ثم يُعاد التخطيط.
  - في الواجهة: رسالتك تظهر فورًا كبالون مستخدم في الـchat (بشارة صغيرة "steered") + سطر في Rail "orchestrator re-planning…" + خطوات الـPlan الجديدة تدخل بحركة هادئة، المنجزة تبقى ✓.
- **E4 — Pause/Resume**: زر pause صغير في StatusStrip (يظهر أثناء الجري): يوقف بعد الجولة الحالية؛ Orb تبهت (C4). Resume يكمل. (يعتمد حدثَي `agent_paused/agent_resumed` — I5).
- **E5 — لا شيء يتغير عند عدم وجود مهمة**: الـcomposer عادي 100%.

---

## F. جدول ربط كل حدث بما يراه المستخدم

| Event (bus) | يظهر أين | الشكل |
|---|---|---|
| `goal_started` | StatusStrip يتفعّل | Orb + "universe is starting" |
| `goal_planned` | Rail Activity — كتلة Plan | checklist |
| `agent_started` | صف agent جديد (working) | C3: "<agent> — <أول فعل متوقع>" |
| `llm_round` | C3 فقط | "…thinking" |
| `tool_requested` | سطر فرعي رمادي queued تحت الـagent | **يعتمد I1: args_summary يُضاف لـtool_running** |
| `tool_running` | السطر يتحول ● + C3 = الأداة + args | — |
| `tool_finished` | السطر ✓ + duration_ms | **يعتمد I2: detail أول 160 حرفًا من النتيجة** |
| `tool_failed` | السطر ✗ rose + error سطر واحد expandable | — |
| `tool_blocked` | تحوّل إلى banner (E2) أو صف blocked | — |
| `handoff` | سهم D3 | — |
| `agent_complete` | صف done ✓ + ملخص سطر | "code agent — done in 14.2s, 7 tools" |
| `skill_activated` | badge D5 | — |
| `memory_updated` | صامت | — |
| `consent_required/decided` | banner E2 | — |
| `provider_retry` | C3: "retrying provider (2/4)…" خافت | — |
| `provider_halt` | StatusStrip error + D8 + زر إعدادات | — |
| `context_compacted` | tooltip على صف الـagent | — |
| `recovery_added` | سطر "recovered: <detail>" رمادي في النشاط | — |
| `goal_completed/failed/cancelled` | Orb تتلاشى + سطر خلاصة C4 داخل الرسالة | — |
| `chat_reply` | **نص الفقاعة بـmarkdown** (F1) | headings/code/جداول + زر نسخ |
| `browser_*` | أدوات عادية + شارة live في تبويب Browser | — |

**F1 — جديد**: رد الـagent يُعرض Markdown كاملاً مع code blocks قابلة للنسخ — لأول مرة (اليوم pre-wrap خام).

**F2 — ملاحظة dedupe**: إصلاح I9 (إصدار `chat_reply`/`goal_failed` مرة واحدة) ضروري قبل هذا الجدول وإلا تتضاعف الأسطر.

---

## G. استدعاء الأدوات الذي استدعته الـagents — حالة اليوم مقابل الجديد

- اليوم: ToolCards قابلة للطي داخل بطاقة الرسالة + StatusLine دوّار. **كله يُلغى من الـchat**.
- الجديد: لا شيء من الأدوات داخل الـchat إطلاقًا؛ الأسطر الفرعية في Rail (D2) هي العرض الوحيد، بتفاصيل inline-expand عند الضغط. args كبيرة تُعرض معروضة monospace بحد أقصى 8 أسطر + "show more".

---

## H. المعاينة (Preview surface) — داخل Rail، صفر نوافذ

تبويب **Preview** في Rail (رأسه: أيقونة النوع + الاسم + المسار + [open externally] [close]). يفتح بالضغط على أي chip/نتيجة من أي مكان.

- **H1 — chips المخرجات داخل الـchat**: كل artifact (ملف من file.write، رابط، صورة، فيديو) = سطر مضمّن بنهاية رد الـagent: أيقونة النوع + الاسم + الحجم. ليس بطاقة — سطر نصي بأيقونة. ضغطة → Preview في Rail.
- **H2 — المصفوفة حسب النوع**:

| النوع | المعاينة داخل Preview | backend |
|---|---|---|
| نص/كود (md, py, tsx…) | عارض code highlight؛ md يُرندر | `GET /api/file?path=` جديد |
| صور png/jpg/webp/gif | img بعرض كامل + zoom ضغطة | نفس endpoint |
| PDF | عارض مضمن (pdf.js) | bytes + header |
| docx/xlsx/pptx | لا معاينة خام المرحلة الأولى؛ لاحقًا "convert preview" → HTML | endpoint تحويل (اختياري M5) |
| html/موقع محلي | iframe sandboxed يخدم من جذر محروس (`/api/preview/…`) مع live-reload عند إعادة الكتابة | endpoint serving محصور |
| صوت | مشغل mp3/wav | نفس endpoint |
| فيديو | مشغل مع HTTP Range | نفس endpoint + Range |

- **H3 — الأمان إلزامي**: endpoints الملفات تخدم **فقط** داخل `project_root` (نفس حارس `_within_scope` في connectors/builtin/files.py) + token الـbridge. لا traversal. iframe الـHTML: sandbox="allow-scripts" على origin معزول عبر port مختلف أو srcdoc — لا وصول لـ`window.universe`/token.
- **H4 — تبويب Files**: artifacts المهمة الحالية + آخر 10 مهام، بحث بالاسم، حجم/تعديل، ضغطة → Preview، زر خارجي. (يحيي FilesPanel الميت بمنطقه المحفوظ.)
- **H5 — الفتح الخارجي**: فعل صريح من زر، يفتح تطبيق/متصفح النظام — وليس نافذة داخلية.

---

## I. المتصفح الحي (قرارك: بث مستمر screencast)

- **I1 — القناة**: `Page.startScreencast` (CDP) على tab المتصفح الذي يسوقه الـbrowser agent → JPEG frames ~2fps (جودة/معدل متدرجان: 4fps أثناء فعل، 0.5fps idle، **تتوقف كليًا بلا مشاهد متصل** — H5 من قبل). الترحيل عبر WS جديد `GET /api/browser/stream?session=<id>` منفصل عن `/api/events` حتى لا يخنق أحداث bus. (يعتمد I8 في §J.)
- **I2 — تبويب Browser في Rail**: canvas يعرض الـframes + شريط tabs علوي مصغّر (تبديل tabs الـagent) + عنوان URL الحالي + حالة ("agent is driving ●"). الضغط على أي frame يفتح نسخة بحجم كامل في تبويب Preview (self-contained — ليست نافذة).
- **I3 — Observe فقط الآن**: المشاهدة passive؛ أي click من المستخدم معطّل. وضع Assist (التحكم اليدوي عبر `Input.dispatch*`) يُترك للمرحلة الأخيرة إن طلبته.
- **I4 — CDP attach لمتصفحك**: إن استخدم الـagent `BrowserConnect` يعمل البث أيضًا (نفس بروتوكول CDP) مع شارة "your browser".
- **I5 — الفشل موثق**: `BrowserClick` فاشل → لقطة auto ترفق بسطر الخطأ thumbnail → يفتح Preview.

---

## J. إصلاحات الـbackend المطلوبة (event schema وAPIs)

| # | الإصلاح | الملف |
|---|---------|-------|
| I1 | `tool_running` يضيف `args_summary` سطري redacted | connectors/builtin/__init__.py (~54) |
| I2 | `tool_finished` يضيف `detail` أول 160 حرفًا من النتيجة (redacted) | نفس الملف |
| I3 | مخرجات `Write` تُسجَّل artifacts حيًا (event `artifact_created {path, bytes, tool, kind}`) بدل انتظار نهاية الهدف — أساس chips H1 | base_agent.py attach_tool_events + orchestrator |
| I4 | `handoff.to_agent` تُملأ من الخطة (orchestrator يعرف التالي) بدل مخرجات الموديل null-غالبًا | core/agent/orchestrator.py (~701) |
| I5 | أحداث `agent_paused`/`agent_resumed`/`step_skipped` لدعم E3/E4 | orchestrator.py |
| I6 | `POST /api/session/{id}/steer` + حقن في سياق orchestrator + replan عند أقرب نقطة قرار | app.py + orchestrator.py |
| I7 | `GET /api/file` (bytes+Range) · `GET /api/preview/…` (HTML محروس) · تحويل docx/xlsx لاحقًا | app.py — حراسة H3 |
| I8 | WS `/api/browser/stream` + lifecycle الـscreencast lazy + relay frames | app.py + connectors/builtin/browser.py |
| I9 | dedupe: `chat_reply` و`goal_failed` تصدر مرة واحدة (إزالة ازدواجية المرآة) | observability/timeline.py + orchestrator |
| I10 | `goal_completed` (ladder) يحمل `reply`/`summary` كاملين بدل detail JSON خفيف | orchestrator/timeline |
| I11 | `memory_updated` يُخفت من bus؟ — لا يبقى كما هو (لا ضرر)، لكن UI يتجاهله (D6) | — |

**قيد**: الـbus contract مشترك مع CLI/tests — كل جديد additive فقط؛ لا إعادة تسمية لأي حدث قائم. الـtests المسمّاة في التقرير (`test_desktop_bridge.py`, e2e) تتوسع تغطيةً فقط.

---

## K. مراحل التنفيذ (كل مرحلة مستقلة التسليم)

| المرحلة | المحتوى | تعتمد على |
|---|---|---|
| **M1 — الواجهة النظيفة** (frontend-only) | حذف B1–B6 · StatusStrip + UniverseOrb C · Rail بـActivity/Files (بيانات الأحداث الموجودة، مع fallbacks) · ConsentBanner E2 · markdown F1 | — |
| **M2 — events غنية** | I1–I4، I9–I10 في الـbackend ثم ترقية العرض | M1 |
| **M3 — سطح المعاينة** | I3، I7 + chips H1–H5 | M1 |
| **M4 — التحكم** | I5–I6 + steer/pause UI (E3–E4) | M2 |
| **M5 — المتصفح الحي** | I8 + تبويب Browser (I1–I5) | M3 |
| **M6 — الصقل** | docx/xlsx preview، Assist mode، diff، B7 (لو اخترت ج) | — |

**تدفق Open Design**: بعد موافقتك: أشغّل خادم Open Design وأبني قبل كود M1 نموذج HTML حي (StatusStrip + Rail + Orb بحركة mock) لتعاينه في المتصفح وتعدّل، ثم يُجمَّد مرجعًا للتنفيذ. (الخادم كان مغلقًا أثناء التحليل.)

---

## L. قرارات متبقية تحتاج ردك فقط

1. **B7** — مجرة الذاكرة الكاملة: (ب) حذف من الواجهة الآن، أم (ج) زر Topbar لسطح كامل، أم (أ) خلفية خافتة في Activity؟
2. **D1** — ألوان agents الهادئة لصفوف Rail فقط (المجرة ثابتة كما طلبت) — موافق؟
3. **E3** — steer يُطبق عند أقرب نقطة قرار (الأداة الجارية تكمل جولتها) بدل قطع حاد — موافق؟
4. **I3** — سطر الـsteer في الـchat يظهر برمز "steered" صغير — مطلوب؟
5. أي بند: اذكر رقمه مع ✔️/✖️/🔄.

> بعد ردك أجمع "SPEC-FINAL" وأبدأ M1 عبر تدفق Open Design.
