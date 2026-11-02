# Universe Agent — أجوبة الأسئلة التوضيحية قبل القرار النهائي

> هذا الملف يجيب عن أسئلتك الستة حرفيًا، من الكود الحقيقي، بدون أي قرارات جديدة.
> بعد قراءته نعود إلى `UI-STATUS-MASTER-SPEC.md` لتخبرني: تعديل / حذف / إضافة.

---

## س1 — شريط الحالة العلوي (StatusStrip): أين هو بالضبط؟ كيف يظهر؟ هل يفتح نوافذ؟

**الموقع بالضبط:**
شريط رفيع بارتفاع ~40px، يجلس **فوق عمود المحادثة مباشرة وتحت الـTopbar**، وبعرض عمود الـchat نفسه فقط (لا يمتد فوق Sidebar ولا فوق Right Rail). أي أنه جزء من العمود الأوسط، أول عنصر فيه.

```
Topbar ("Working…" + أزرار)
─────────────────────────────────
[ Orb 28px ]  code agent — running tests…   [pause]   ← StatusStrip
─────────────────────────────────
فقاعات المحادثة…
─────────────────────────────────
Composer
```

**كيف يظهر:**
- مخفي تمامًا عندما لا توجد مهمة جارية.
- يظهر بتلاشي سريع 150ms لحظة `goal_started`، ويبقى ثابت الارتفاع طوال المهمة.
- محتواه عنصران فقط: مجرة Orb الصغيرة يسارًا + سطر نص واحد حي يمينها ("ما يفعله universe الآن") + زر pause صغير أقصى اليمين.
- عند انتهاء المهمة يتلاشى خلال 600ms ويُكتب بدلًا منه سطر خلاصة واحد داخل الرسالة ("done in 18.4s · 7 tools").

**هل يفتح أي نوافذ؟**
**لا. إطلاقًا.** لا click يفتح شيئًا منه. هو سطح عرض فقط (read-only) ما عدا زر pause الذي يوقف/يستأنف المهمة في مكانها. لا modal، لا popup، لا توسيع، لا نافذة. أي تفصيل إضافي يعيش في Right Rail وليس هنا.

---

## س2 — هل تُستدعى الـcore agents معًا في نفس الوقت أم واحدًا تلو الآخر؟

**الجواب من الكود (`core/agent/orchestrator.py`): واحد تلو الآخر دائمًا، تتابع صارم.**

- الـorchestrator يبني **خطة مرتبة** (`_default_plan`): قائمة خطوات مثل `planning → code → review`.
- ثم ينفذها في حلقة `for` عادية — **لا يوجد أي `asyncio.gather` أو `TaskGroup` أو `create_task` في الملف كله** (تم البحث والتأكد).
- كل خطوة: يستدعي الـagent، **ينتظر اكتماله الكامل** (`await get_agent(agent_name).run(...)`)، يستلم منه `HandoffPackage`، ثم ينتقل للخطوة التالية حاملًا الـhandoff معه.
- الـagents لا يعرفون بعضهم ولا ينادون بعضهم. الـorchestrator هو الوحيد الذي ينقل العمل بينهم عبر `HandoffPackage` (يحمل: `what_was_done`, `key_decisions`, `artifacts_created`, `avoid_these`, `next_agent_focus`, `tools_used`).
- مثال حقيقي لخطة توليد كود ويب: `planning` (تفكيك، ميزانية 4 جولات) → `research` (اختياري، إن احتاج بحثًا خارجيًا) → `code` (التنفيذ) → `review` (التحقق). كل واحد يبدأ بعد نهاية السابق.
- كل agent داخل خطوته يعمل **حلقة أدوات** (`run_tool_loop`) حتى 16 جولة (24 للمهام المعقدة/الميديا): ينادي LLM ← ينفذ أداة ← يقرأ النتيجة ← يعيد الكرة — ثم يرجع handoff JSON واحدًا في النهاية.

**الخلاصة:** في أي لحظة يوجد **agent واحد نشط فقط**. لهذا السبب Orb واحدة في الواجهة كافية، وصفوف الـRail تُظهر السابقين كـdone واللاحقين كـwaiting.

---

## س3 — هل يملك الـcore agents القدرة على استدعاء وصنع subagents بعدد غير محدود؟

**الجواب: لا. هذه القدرة غير موجودة إطلاقًا في الكود الحالي.**

- تم البحث في `core/` و`connectors/` كاملة عن `subagent`, `sub_agent`, `delegate`, `Agent`: **صفر نتائج** (النتيجة الوحيدة كانت تعليقًا عن crash-loop supervision لا علاقة له).
- الـagent لا يملك أي أداة تفويض: لا توجد أداة `delegate`, `spawn_agent`, `call_subagent` في سجل الأدوات الـ98.
- ما يملكه الـagent فعليًا: مناداة **أدوات** من قائمته البيضاء فقط (whitelist)، وقراءة skill واحد إجباري قبل البدء (`Skill` + `Skill` فيما يسمى preflight)، ثم حلقة الأدوات، ثم handoff JSON.
- العدد "غير المحدود" الوحيد الموجود هو **عدد جولات الأدوات داخل الخطوة الواحدة** (16–24 جولة)، وليس عدد الـagents.
- ملاحظة دقيقة: حقل `next_agent` في الـhandoff يوحي أن الـagent "يختار التالي"، لكنه في الواقع **يُتجاهل** — الـorchestrator يسير على خطته المحددة سلفًا، وقيمة `next_agent` القادمة من الموديل تكون null في الغالب.

**الخلاصة:** البنية اليوم = orchestrator واحد + 12 متخصصًا يُستدعون تتابعيًا. لا شجرة subagents، لا تفريع، لا توازي.

---

## س4 — ماذا قصدت بالضبط في D7 (تخطي agent) وفي D8 (provider halt)؟

### D7 — زر ⏹ بجانب سطر الـagent = "تخطَّ هذه الخطوة"

- **ليس قتلًا قسريًا** لعملية بايثون، وليس إيقافًا للمهمة كلها.
- معناه: "يا orchestrator، تجاوز هذا الـagent الحالي وانتقل للخطوة التالية في الخطة".
- مثال: الخطة `planning ✓ → code ● → review ○` وأنت ترى `code` يضيع وقتًا في اتجاه خاطئ → تضغط ⏹ على سطر `code` → يتوقف عند نهاية جولته الحالية، تُوسم الخطوة `skipped` في الـPlan، ويبدأ `review` (أو يُعاد التخطيط إن كانت الخطوة المتخطاة أساسية).
- يحتاج حدث backend جديد (`step_skipped`) — اليوم لا يوجد، والإيقاف الوحيد الموجود هو إلغاء الهدف كله.

### D8 — شريط `provider_halt` = "توقف المصنع بصدق"

- يحدث عندما **يفشل مزود الـLLM نفسه** (لا مفتاح API، خطأ مصادقة، تجاوز حصة، انقطاع اتصال) **مرتين متتاليتين** — عندها يعرف الـorchestrator أن كل agent لاحق سيفشل بنفس السبب، فيوقف الخطة كلها بدل حرق 9 خطوات فاشلة.
- ما يراه المستخدم اليوم: بطاقة خطأ حمراء داخل الرسالة + زر "Open settings".
- ما سيصبح: نفس الشريط الأحمر الهادئ لكن **أعلى تبويب Activity في Rail** + سطر الخطأ في StatusStrip + زر إعدادات inline — بلا أي نافذة.
- الفرق عن `tool_failed`: `tool_failed` = أداة واحدة فشلت (يُكمل الـagent بغيرها)؛ `provider_halt` = المحرك نفسه ميت (تتوقف المهمة كلها).

---

## س5 — كيف يأتي الـsteer في E3؟ هل هو زر جديد؟

**لا. ليس زرًا جديدًا إطلاقًا. هو نفس زر الإرسال في الـcomposer، يتبدل سلوكه تلقائيًا.**

**الوضع اليوم (للمقارنة):**
اليوم أثناء مهمة جارية زر الإرسال **يتحول حرفيًا إلى زر إيقاف مربع** (`Composer.tsx`: `if (canStop) { cancelGoal(); return; }`)، وزر Esc يلغي المهمة. أي أنك **لا تستطيع أصلًا كتابة أي شيء أثناء الجري** — إما تنتظر أو تلغي كل شيء.

**الوضع الجديد (E3):**
- أثناء الجري يبقى الـcomposer قابلًا للكتابة، وفوقه شارة صغيرة توضح الوضع: `"Steering running session… — Enter sends to orchestrator"`.
- تكتب توجيهك ("لا، استخدم الطريقة الفلانية بدل هذه") وتضغط Enter كالمعتاد.
- الرسالة **لا تفتح sessionًا جديدًا**. تُرسل إلى endpoint جديد `POST /api/session/{id}/steer`.
- الـorchestrator يستلمها **عند أقرب نقطة قرار بين خطوات الخطة** (لا يقطع أداة في منتصف تنفيذها — الأداة الجارية تكمل جولتها الحالية أولًا)، فيفهم: أين توقفت المهمة، ولماذا أوقفتَ المسار، وماذا تريد بدلًا منه — ثم **يعيد توليد بقية الخطة بالكامل** من تلك النقطة.
- ما تراه: رسالتك تظهر فورًا كبالون مستخدم عادي في الـchat (مع شارة صغيرة "steered") + سطر في Rail: "orchestrator re-planning…" + خطوات الـPlan القديمة المتبقية تُستبدل بالجديدة بحركة دخول هادئة، والمنجزة تبقى ✓.
- لا Esc جديد، لا اختصار جديد، لا زر جديد. الإرسال العادي هو الـsteer أثناء الجري، والإرسال العادي هو session جديد عند السكون.

---

## س6 — قائمة الـcore agents الـ12 الكاملة: الأدوات والـskills بدقة

> المصدر: `core/core_agents/agents.py` (القوائم البيضاء الحرفية) + `skill/taxonomy.py` (خريطة `AGENT_OWNERSHIP`) + قاعدة البيانات الحية (`~/.universe/universe.db`: **1580 skill موثوقة**، **98 أداة** في `tool_catalog`).
>
> آلية الـskills (مهمة للفهم): كل agent قبل بدء خطوته **مجبور** على preflight — يبحث (`Skill`) عن أنسب skill لملكيته ثم يقرأها (`Skill`) ثم يسجل استخدامها (`Skill`). الملكية الأساسية لكل agent = نظام شمسي واحد + مدار واحد (جدول `skill_agent_ownership`: كل agent يملك سطرًا واحدًا `primary`). لكن البحث الفعلي يجلب أفضل 3–5 skills من أي مكان عند الحاجة.

### 1) planning — مخطط المهام وتفكيكها
- **الدور:** يبني أصغر خطة كاملة: جرد القدرات، فجوات، بحث إجباري، مقارنة بدائل، معايير قبول لكل مخرج، حدود أمان، نقاط موافقة.
- **النظام/المدار:** `operations_delivery` / `planning`.
- **الأدوات (8):** `MemoryQuery`, `CapabilityCatalog`, `LS`, `Read`, `Bash`, `WebSearch`, `WebFetch`, `WebResearch` (+ أدوات الـskill الثلاث المشتركة للجميع: `Skill`, `Skill`, `Skill`).
- **الـskills:** نظام `operations_delivery` فيه **153 skill** حية (تخطيط، إدارة مشاريع، أتمتة مساحة العمل).

### 2) research — البحث والتحليل
- **الدور:** يبحث الويب قبل أي تنفيذ، يقارن أفضل المناهج والأدوات والرخص، يرجع توصية موثقة بالمصادر والبدائل المرفوضة وخطوات التحقق. يعامل كل محتوى ويب كـ`[UNTRUSTED:web]`.
- **النظام/المدار:** `research_intelligence` / `WebResearch`.
- **الأدوات (10):** `WebSearch`, `WebFetch`, `WebResearch`, `BrowserNavigate`, `BrowserExtract`, `BrowserSnapshot`, `LS`, `Read`, `MemoryQuery`, `CapabilityCatalog`.
- **الـskills:** نظام `research_intelligence` فيه **35 skill** (بحث ويب، أدبيات، تحليل مصادر، عمليات بحثية).

### 3) code — مهندس التنفيذ البرمجي
- **الدور:** ينفذ التغيير وينتج artifacts ملموسة يتحقق منها بأوامر حقيقية. ممنوع عليه الخلط بين "أداة مقترحة" و"أداة مسجلة ومثبتة".
- **النظام/المدار:** `software_engineering` / `backend`.
- **الأدوات (17):** `Read`, `Write`, `Edit`, `Bash`, `GitStatus`, `GitDiff`, `GitLog`, `GitCommit`, `GitPush`, `CodeExecute`, `MemoryQuery`, `CapabilityCatalog`, `LS`, `VisionAnalyze`, `WebSearch`, `WebFetch`, `WebResearch`.
- **الـskills:** نظام `software_engineering` فيه **247 skill** (لغات، أطر، frontend/backend، جودة، اختبار).

### 4) file — معالجة المستندات
- **الدور:** يفحص قدرات الملفات أولًا، يختار أفضل طريقة تحويل/تحرير مدعومة، يحافظ على التنسيق والبيانات الوصفية، ويتحقق بإعادة الفتح.
- **النظام/المدار:** `operations_delivery` / `workspace_automation`.
- **الأدوات (6):** `Read`, `Write`, `LS`, `Delete`, `Archive`, `ImageOcr`, `MemoryQuery`, `CapabilityCatalog`.
- **الـskills:** من نظام `operations_delivery` (153) — مدار أتمتة مساحة العمل والملفات.

### 5) write — الكتابة والتوثيق
- **الدور:** ينتج المستند المطلوب بلغة المستخدم وأسلوبه، بلا تحويل ادعاءات غير مثبتة إلى حقائق، مع حفظ المصادر وذكر عدم اليقين.
- **النظام/المدار:** `operations_delivery` / `writing_communications`.
- **الأدوات (4 فقط — الأضيق):** `Read`, `Write`, `MemoryQuery`, `CapabilityCatalog`.
- **الـskills:** من نظام `operations_delivery` (153) — مدار الكتابة والاتصالات.

### 6) design — التصميم البصري UI/UX
- **الدور:** يفحص التصميم الحقيقي وقدرات المتصفح، ينتج مخرجات لا أوصافًا: ملف DESIGN.md بالصيغة 9-أقسام + الأصول المطلوبة + لقطة إثبات بصرية.
- **النظام/المدار:** `product_design` / `ui_ux`.
- **الأدوات (10):** `Read`, `Write`, `SvgGen`, `VisionAnalyze`, `CssTools`, `ColorContrastCheck`, `BrowserNavigate`, `BrowserScreenshot`, `MemoryQuery`, `CapabilityCatalog`.
- **الـskills:** نظام `product_design` فيه **780 skill** — الأكبر بفارق (أنظمة تصميم، ألوان، خطوط، إتاحة، تجربة frontend).

### 7) data — تحليل البيانات والرسوم
- **الدور:** يفحص البيانات والقدرات، يتحقق من المدخلات والافتراضات، ينتج تحليلًا قابلًا لإعادة الإنتاج بأرقام حقيقية ومصادر وفحوصات.
- **النظام/المدار:** `data_intelligence` / `data_analysis`.
- **الأدوات (7):** `Read`, `Bash`, `Charting`, `PandasQuery`, `DataValidate`, `MemoryQuery`, `CapabilityCatalog`.
- **الـskills:** نظام `data_intelligence` فيه **44 skill** (تحليل، تصور، SQL، منصات مثل DuckDB/ClickHouse).

### 8) review — ضمان الجودة والمراجعة
- **الدور:** سلطة القبول. يفحص كل artifact بأدوات حقيقية، يشغل الاختبارات، يرفض السرد غير المثبت بخطوات إصلاح محددة، ولا يعتمد إلا بالدليل.
- **النظام/المدار:** `software_engineering` / `testing_quality`.
- **الأدوات (10):** `Read`, `LS`, `Edit`, `Bash`, `TestRunner`, `CodeAnalyzer`, `CoverageReport`, `VisionAnalyze`, `MemoryQuery`, `CapabilityCatalog`.
- **الـskills:** من نظام `software_engineering` (247) — مدار الاختبار والجودة وTDD.

### 9) security — المراجعة الأمنية
- **الدور:** يطبق AgentShield وOWASP، يقلل الإيجابيات الكاذبة، يعيد إنتاج النتائج أو يستشهد بـCVE موثوق، ويبلغ بالمخاطر المتبقية والعلاج. لا يعتمد "نظيفًا" بلا دليل.
- **النظام/المدار:** `security_trust` / `security_audit`. **الأغلى نموذجًا** (`expensive` tier).
- **الأدوات (8):** `Read`, `Bash`, `StaticAnalysis`, `CodeAnalyzer`, `VulnScanner`, `SecretScanner`, `DependencyAudit`, `MemoryQuery`, `CapabilityCatalog`.
- **الـskills:** نظام `security_trust` فيه **90 skill** (تدقيق، أمن تطبيقات، هويات، خصوصية).

### 10) browser — أتمتة الويب والاستخراج
- **الدور:** ينفذ أفعال متصفح مرئية حقيقية لا أوصافًا (تنقل، نقر، تعبئة، استخراج، لقطات)، يحترم robots.txt وحدود تسجيل الدخول، ويتحقق من اللقطات وحالة DOM قبل إعلان النجاح.
- **النظام/المدار:** `browser_automation` / `browser_control`. **الأرخص نموذجًا** (`cheap` tier).
- **الأدوات (12):** `BrowserNavigate`, `BrowserClick`, `BrowserFill`, `BrowserSnapshot`, `BrowserScreenshot`, `BrowserExtract`, `BrowserConsole`, `BrowserTabs`, `BrowserUpload`, `BrowserConnect` (ربط CDP بمتصفح المستخدم — يتطلب موافقة صريحة)، `MemoryQuery`, `CapabilityCatalog`.
- **الـskills:** نظام `browser_automation` فيه **17 skill** (تحكم، اختبار QA، استخراج/OCR).
- **ملاحظة البث:** يعمل على Playwright chromium مدمج. اللقطات اليوم فقط عند استدعاء `BrowserScreenshot` صراحة — لا لقطات تلقائية بعد النقر، ولا screencast. البث الحي يتطلب الإضافة المقترحة.

### 11) api — التكاملات والموصلات
- **الدور:** أمين الموصلات. يفحص الكتالوج أولًا، يقارن الـAPIs والـschemas والصلاحيات، يكتشف قدرات MCP ويسجلها بصدق، ويتحقق من استجابات حقيقية مطابقة للـschema. لا يسجل مفاتيح في اللوجز.
- **النظام/المدار:** `integration_protocols` / `api_design`.
- **الأدوات (40+ — الأوسع):** `HttpClient`, `OpenapiParser`, `GraphqlClient`, `LS`, `Read`, `Bash`, `WebSearch`, `WebFetch`, `WebResearch`, `MemoryQuery`, `CapabilityCatalog` + **30 موصلًا خارجيًا أصليًا**: `github_repo_info`, `github_create_issue`, `gitlab_project_info`, `pypi_package_info`, `npm_package_info`, `crates_package_info`, `slack_post_message`, `slack_list_channels`, `discord_webhook_post`, `telegram_send_message`, `notion_query_database`, `notion_create_page`, `linear_list_issues`, `linear_create_issue`, `trello_list_boards`, `trello_create_card`, `asana_list_tasks`, `asana_create_task`, `jira_search_issues`, `jira_create_issue`, `dropbox_list_files`, `google_drive_list_files`, `stripe_list_charges`, `stripe_create_charge`, `hubspot_create_contact`, `airtable_list_records`, `wikipedia_search`, `arxiv_search`, `coingecko_price`, `openweathermap_current` + جسر MCP العام: `McpAddServer`, `McpListServers`, `McpRemoveServer`, `McpCallTool`.
- **الـskills:** نظام `integration_protocols` فيه **65 skill** (تصميم APIs، GraphQL، MCP، موصلات).

### 12) devops — البنية والنشر
- **الدور:** يفحص البنية الحقيقية، يقارن البدائل الآمنة، يُظهر الأفعال اللارجعة، يعمل بأقل صلاحية، ويتحقق من الصحة والـrollback واللوجز. يعامل الإنتاج كإنتاج.
- **النظام/المدار:** `platform_operations` / `devops`.
- **الأدوات (12):** `Bash`, `GitStatus`, `GitDiff`, `GitLog`, `GitCommit`, `GitPush`, `DockerBuild`, `DockerRun`, `DockerPs`, `Ssh`, `K8s`, `LogTail`, `MemoryQuery`, `CapabilityCatalog`.
- **الـskills:** نظام `platform_operations` فيه **148 skill** (بنية، نشر، مراقبة، SRE، CI/CD).

### + orchestrator — المنسق (ليس agent متخصصًا)
- **الدور:** يصنف الهدف، يبني الخطة المرتبة، ينفذ الخطوات تتابعيًا، ينقل الـhandoffs، يطبق حراس المزود، يبني الملخص النهائي، ويحفظ كل شيء (checkpoints + timelines + memory). لا أدوات له مباشرة — أدواته هي الـagents أنفسهم.
- **خطط جاهزة حسب التصنيف:** كتابة→`write+review`، كود ويب→`planning+code+review` (+`research` عند الحاجة)، API→`api+review`، تصميم→`design+review`، بيانات→`data+review`، بحث→`research+write`، devops→`devops+review`، أمن→`security+review`، مستندات→`file+review`، ميديا→`research+api+code+review`.

---

## جدول الأنظمة الشمسية العشرة (أين تسكن الـ1580 skill)

| النظام الشمسي | عدد الـskills الحية | الـagent المالك |
|---|---|---|
| `product_design` | 780 | design |
| `software_engineering` | 247 | code (backend) + review (testing_quality) |
| `operations_delivery` | 153 | planning + file + write |
| `platform_operations` | 148 | devops |
| `security_trust` | 90 | security |
| `integration_protocols` | 65 | api |
| `data_intelligence` | 44 | data |
| `research_intelligence` | 35 | research |
| `browser_automation` | 17 | browser |
| `agent_meta` | 1 | — (ميتا، بلا مالك) |

> كل الـ1580 بحالة `trusted`. كل agent يملك سطر ملكية `primary` واحدًا (12 سطرًا في `skill_agent_ownership`).

---

## الخلاصة بجملة واحدة لكل سؤال

1. **StatusStrip:** شريط 40px فوق المحادثة تحت Topbar، عرض فقط + pause، **صفر نوافذ**.
2. **الاستدعاء:** واحد تلو الآخر دائمًا — لا توازي في الكود.
3. **Subagents:** غير موجودة إطلاقًا — لا تفويض، لا عدد غير محدود.
4. **D7:** تخطي خطوة agent لصالح التالي (لا قتل). **D8:** موت مزود LLM نفسه فيوقف المصنع كله.
5. **Steer:** نفس زر الإرسال، يتبدل سلوكه أثناء الجري — لا زر جديد.
6. **القائمة:** 12 متخصصًا أعلاه + orchestrator منسق، بالأدوات الحرفية والملكيات والأعداد الحية.
