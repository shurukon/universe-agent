# Universe Agent — خطة الإصلاح الشاملة (REPAIR PLAN)

**التاريخ:** 2026-11-10 · **المستودع:** `D:\universe\universe-agent` @ `af0bc8e` (2026-11-05)
**المؤلف:** shurukon · **مراجعة سابقة:** `docs/STRUCTURE_REVIEW.md` (2026-11-06) + مراجعة مستقلة بخمسة subagents

---

## 0. قرارات المستخدم الملزِمة (نقطة انطلاق الخطة)

| # | القرار | الأثر على الخطة |
|---|---|---|
| D1 | **حذف الأدوات الـ14 اليتيمة كليًا** من المشروع (لا تُدرج في whitelist، لا تبقى في السجل) | المرحلة 1 — مع إعادة كتابة الاختبارات التي تستخدم `TestRunner` كـ probe |
| D2 | **حذف منطق الوكيل القديم (goal) نهائيًا**؛ المحرك الافتراضي (session) هو الرسمي الوحيد | المرحلة 4 — هجرة ثم حذف، ثم `v0031` ينقل حاوية المهام من `goals` إلى `sessions` |
| D3 | **لا دمج أي أدوات Meta بعد الآن** — الهاكاثون انتهى | المرحلة 2 — حذف واجهة Meta الوسائطية وادعاءاتها؛ **بقاء** مزوّد Meta كـ LLM preset (ليس "أداة") وبقاء STT لأنه حيّ |
| D4 | **المجرة ثلاثية الأبعاد تبقى** — سنحتاجها لاحقًا | المرحلة 5 — المجرة تصبح مُغذّاة ببيانات ذاكرة حقيقية مع إبقاء الإجرائية كـ fallback |
| D5 | **إصلاح كل الأخطاء والمشاكل والتناقضات بلا استثناء** | كل بند من المراجعة مغطّى في مرحلة؛ لا بند "لاحقًا" إلا ما هو خارج نطاق المنتج (وإن ذُكر صراحة) |

**قاعدة التنفيذ:** كل مرحلة تُنهي مستقلة، مع اختبار تحقق واحد على الأقل، وبنك قابل للتراجع. لا يُحذف شيء قبل أن يثبت أنه غير مستهلك.

---

## 1. لوحة النتائج (Definition of Done)

الحالة النهائية المقبولة:

- [ ] `len(registry) == len(whitelist) == 121` — صفر أدوات يتيمة (المُتحقَّق: `security/capability.py:655-661` لم يعد له حالة رفض تُستدعى).
- [ ] `GAX_ENGINE` غير موجود؛ `core/agent/universe_agent.py` لا يحتوي إلا `run_turn` + `resume_turn` (المستند)؛ صفر أسطر مرتبطة بمحرك goal.
- [ ] جدول `goals` غير موجود؛ `sessions` هو مخزن المهام الوحيد؛ `schema` v31 مُطبَّق.
- [ ] لكل استدعاء أداة مجدول: نتيجة واحدة مُلحقة دائمًا؛ وكل نتيجة أداة **تعبر الأدوار** في السجل.
- [ ] التزامن يعمل فعليًا (قراءة متوازية / كتابة منفردة) بسقف تزامن.
- [ ] نافذة السياق = نافذة الموديل الحقيقية؛ `usage` يصل من البث.
- [ ] `provider_halt` يصدر من المسار الافتراضي؛ الإلغاء يكتب `turn_end status=cancelled`.
- [ ] الذاكرة: الجسر يشغّل حلقة subconscious، والجلسة تستدعي L3/L4، والدور يكتب L2.
- [ ] `/api/memory/universe` يعيد بيانات حقيقية، والمجرة ثلاثية الأبعاد ترسمها (وإلا تبقى إجرائية).
- [ ] `POST /api/feedback` يعمل على قاعدة بيانات جديدة.
- [ ] لا مسار جسر ميت بلا مستهلك؛ لا helper ميت في العميل؛ صفر `TODO`-style تناقض في README/SECURITY/UI-OVERHAUL/parity/events-v1.
- [ ] `pytest -m "not llm"` أخضر بلا `-x`، والأرقام في README **مقيسة** من التشغيل لا مكتوبة يدويًا.
- [ ] `npx tsc --noEmit` + `npm run build` خضراء؛ التطبيق يعمل حيًّا (لقطة تحقّق).

---

## 2. خريطة المراحل والتبعيات

```
P0 خط الأساس + بنية الاختبار  ──► P1 حذف الأدوات اليتيمة ──► P2 حذف السطح الميت + Meta
        │                                                          │
        ├──► P3 إصلاحات الـ loop (A1..A9) ◄───────────────────────┘
        │              │
        │              └──► P5 الذاكرة + المجرة (B1..B4)
        │
        └──► P4 حذف محرك goal نهائيًا (هجرة → حذف → v31)
                       │
                       └──► P6 عقد الجسر/الواجهة (أحداث، 401، Electron، إعدادات)
                                      │
                                      └──► P7 تصحيح التوثيق ──► P8 التحقق النهائي والإطلاق
```

| المرحلة | المحتوى | الجهد | يعتمد على |
|---|---|---|---|
| **P0** | خط الأساس + إصلاح بنية الاختبار (fixture، TTL، skip، CI، hang) | 3h | — |
| **P1** | حذف الأدوات الـ14 اليتيمة + إصلاح hook coverage + الأعداد | 4h | P0 |
| **P2** | حذف السطح الميت (11 helper عميل + 18 مسار) + سطح Meta + وثائق الهاكاثون | 5h | P0 |
| **P3** | إصلاحات الـ agent loop (A1..A9) | 14h | P0 |
| **P4** | حذف محرك goal: هجرة → حذف → v31 (`goals`→`sessions`) | 10h | P3 |
| **P5** | الذاكرة حيّة + تغذية المجرة ثلاثية الأبعاد | 12h | P3, P4 |
| **P6** | عقد الجسر/الواجهة: 12 حدثًا، 401/403، Electron، الإعدادات الميتة، ازدواج Muse | 7h | P2, P3 |
| **P7** | تصحيح كل التوثيق (README/SECURITY/UI-*/parity/events-v1/vendor) | 4h | P1..P6 |
| **P8** | التحقق الكامل + إعادة توليد الأرقام + فحص حي | 2h | الكل |
| | **الإجمالي** | **~61h** | |

---

## 3. P0 — خط الأساس وبنية الاختبار (لا تغيير سلوك)

**لماذا أولًا:** كل مرحلة بعده تحتاج "تحقق" موثوقًا؛ حاليًا المجموعة غير hermetic (fixture مفقود، مسار جهازي مثبَّت، hang حقيقي) فلا يمكن إثبات أي إصلاح.

| # | الملف:السطر | التعديل |
|---|---|---|
| P0-1 | `tests/fixtures/muse_echo_run.jsonl` (**جديد**) | 21 سطر JSONL بصيغة MSP الحقيقية: `run.lifecycle.started` + `run.lifecycle.failed` (reason فيه "provider") + `run.lifecycle.turn.completed`. المواصفة: `tests/test_bots.py:96-112` + `bots/runtime.py:233-257` |
| P0-2 | `tests/test_bots.py:97-98` | تصحيح الـ docstring: الـ fixture **مركَّب** (لم يُودع أصلًا)، لا "genuine capture" |
| P0-3 | `universe_cua_helper.py:41-59` | `_load_frame_ttl()`: ترتيب `UNIVERSE_CUA_FRAME_TTL_S` → `UNIVERSE_HOME/.universe/config.json` → 15.0 (احترام `UNIVERSE_HOME` بدل `~` الحقيقي) |
| P0-4 | `tests/test_computer_use.py:27-38` |Broker الاختبار يحقن `UNIVERSE_CUA_FRAME_TTL_S=15` + `UNIVERSE_HOME` → hermetic؛ + اختبار جديد `test_frame_ttl_env_override_wins` |
| P0-5 | `tests/test_parity_round7.py:399-411` | تخطٍّ مشروط بـ`UNIVERSE_RE_EVIDENCE_DIR`؛ التخطي عند غياب الملفات لا عند غياب المجلد فقط |
| P0-6 | `tests/test_task_threading.py:45-47, 76-108, 119-138` | `FakeLLM.complete` يأخذ `on_delta`؛ الـ fixture **لا يدخل** lifespan الجسر ( pumping scheduler )؛ `_wait_for(predicate, timeout_s=10)` بديل polling غير محدود؛ إلغاء مهام الدور قبل `client.close()` |
| P0-7 | `.github/workflows/ci.yml:5-7, 47-53` | إزالة `-x`؛ `--junitxml=pytest-report.xml` + رفع التقرير؛ `UNIVERSE_HOME` إلى runner.temp؛ تصحيح "~550" في الترويسة |
| P0-8 | خط أساس | `python -c "from connectors.builtin import get_registry; print(len(get_registry().names()))"` → 135 · `pytest -m "not llm" --collect-only -q | tail -1` → العدد الحقيقي · `git status` / branch باسم `repair/2026-11-10` |

**اختبارات جديدة:** `tests/test_computer_use.py::test_frame_ttl_env_override_wins`
**تحقق:** `pytest tests/test_bots.py tests/test_parity_round7.py tests/test_computer_use.py tests/test_task_threading.py -q` + `pytest -m "not llm" --collect-only -q | tail -1`
**خطر:** منخفض (اختبارات فقط). **تراجع:** إعادة نقطة P0.

---

## 4. P1 — حذف الأدوات الـ14 اليتيمة (D1)

**الهدف:** `registry == whitelist == 121`. أي أداة مسجّلة يجب أن تكون قابلة للاستدعاء.

### 4.1 مواقع الحذف (definition → cut)

| الأداة | ملف:أسطر | ملاحظة |
|---|---|---|
| `TestRunner` | `connectors/builtin/misc.py:128-137` | **اختبار القائمة البيضاء يستخدمه كـ probe** — يجب إعادة توجيهه (§4.3) |
| `CodeAnalyzer` | `misc.py:139-170` | imports داخل الدالة → لا استيراد يتيم |
| `StaticAnalysis` | `misc.py:172-188` | التعليق المشترك 172 يموت مع `VulnScanner` |
| `VulnScanner` | `misc.py:190-212` | **-policy**: `security/capability.py:117` |
| `SvgGen` | `misc.py:214-224` | `__init__.py:174,175` + `capability.py:113` + `UniverseFlow.tsx:214` + `fold.ts:67` |
| `js` | `misc.py:399-411` | اسم مستعار رقيق لـ`CodeExecute` — لا يفقد الموديل القدرة (js مدعوم في `misc.py:366-367`) |
| `js_reset` | `misc.py:413-422` | stub صادق |
| `js_add_node_module_dir` | `misc.py:424-433` | stub صادق |
| `CoverageReport` | `misc.py:435-449` | `SECURITY.md:182` |
| `SecretScanner` | `misc.py:601-667` | |
| `DependencyAudit` | `misc.py:669-767` | |
| `CssTools` | `vision.py:141-217, 293-298` + `_hex_to_rgb:34-43` + `import re:24` + `__init__.py:175` + `UniverseFlow.tsx:268` + `fold.ts:68` | |
| `ColorContrastCheck` | `vision.py:219-284, 299-304` + helpers `221-248` | `import re:24` يصبح غير مستخدم (ruff F401) |
| `BashUnsandboxed` | `shell.py:192-200` **فقط** | ⚠️ **يجب البقاء على `shell_exec_unsandboxed` (`shell.py:160-180`)** — يستورده الجسر في `app.py:3245-3247` لـ`/api/terminal` |

### 4.2 المراجع التي تُحرَّر

- `connectors/builtin/__init__.py:121-128` (مجموعات `_SHELL_TOOLS`/`_WRITE_TOOLS` + التعليق)، `:174-175` (قائمة أسماء شريحة `artifact_created`)، `:286` (`"BashUnsandboxed": 600`).
- `security/capability.py:113` (تعليق SvgGen)، `:117` → `AUTO_MODE_NEVER_LIFT = frozenset({"Delete", "Archive"})`.
- `connectors/builtin/misc.py:1-13` و`vision.py:1-20` (ترويسة الوحدة تعداد أدوات محذوفة).
- `connectors/builtin/shell.py:6-9` (ترويسة "unsandboxed = أداة منفصلة" → "متاح فقط عبر جسر الطرفية")، `:108,:135` (تعليقات تسمّي TestRunner).
- `universe_desktop_bridge/app.py:3201-3202` (تعليق).
- العميل: `client/src/components/flow/UniverseFlow.tsx` (14 سطر أيقونة)، `client/src/lib/flow/fold.ts:42,49,56,67,68`، `client/src/lib/flow/ztimeline.ts:76`.
- التوثيق: `docs/SECURITY.md:178,182`، `docs/UI-STATUS-ANSWERS.md:137,149,155` (كتالوجات الأدوار القديمة → وسم "legacy roster — متقاعد").

### 4.3 إصلاح hook coverage (ثغرة أمنية — نفس المرحلة)

**الثغرة:** السجل يوجّه `pre_shell` إلى `{Bash, BashUnsandboxed, PowerShell, CodeExecute, Monitor}` و`pre_write` إلى `{Write, Edit, ApplyPatch, NotebookEdit}` (`__init__.py:123-128`)، لكن `hooks/manager.py:22-25` `_EVENT_TOOLS` يغطي `("Bash","PowerShell","CodeExecute")` و`("Write","Edit")` فقط ⇒ **`Monitor` و`ApplyPatch` و`NotebookEdit` تتجاوز بوابة fact-guard وكل line hooks للمستخدم** — Contrary to the claim in `__init__.py:119-122`.

**الإصلاح (مصدر حقيقة واحد):**
```python
# hooks/manager.py:18-25
PRE_SHELL_TOOLS = ("Bash", "PowerShell", "CodeExecute", "Monitor")
PRE_WRITE_TOOLS = ("Write", "Edit", "ApplyPatch", "NotebookEdit")
_EVENT_TOOLS = {"pre_shell": PRE_SHELL_TOOLS, "pre_write": PRE_WRITE_TOOLS}
```
- `connectors/builtin/__init__.py:123-128` يستوردها (داخل الدالة لتفادي circular import).
- `hooks/builtin_factguard.py:31`: matcher `"Bash"` → `"*"` (كي يغطي `PowerShell`/`CodeExecute`/`Monitor`).

### 4.4 إصلاح الاختبارات

| الملف:السطر | التعديل |
|---|---|
| `tests/test_tool_whitelist.py:19-32, 85-101` | استبدال `TestRunner` بأداة اصطناعية خارج whitelist (نمط `tests/test_security_hardening_2026_10.py:102-109`: `Tool(name="__NotWhitelisted__", capability="Read")` ← assert `blocked_by == "whitelist"`) — **لا تعِد TestRunner للقائمة** |
| `tests/test_parity_round7.py:295` | إزالة `"js","js_reset","js_add_node_module_dir"` من تأكيد الأسماء |
| `tests/test_phase3_tools.py:159-174, 203-245, 248-280, 508-531, 534-557` | حذف 6 اختبارات الأدوات اليتيمة |
| **جديد** `tests/test_hooks_coverage.py` | (أ) `_EVENT_TOOLS` يطابق مجموعات السجل؛ (ب) استدعاء `Monitor` يُطلق `pre_shell` فعليًا (monkeypatch على `get_hook_manager().check`) |
| **جديد** `tests/test_tool_registry_invariants.py` | `set(registry.names()) == set(get_agent("universe").tool_whitelist_names)` — يمنع عودة اليتيم |

### 4.5 الأعداد

`README.md:16` و`:110`: `132` → `121`. `core/core_agents/agents.py:362-364`: تصحيح تعليق "every capability they held" (design مستثنى).

**تحقق:**
```
python -c "from connectors.builtin import get_registry; from core.core_agents import get_agent; r=set(get_registry().names()); w=set(get_agent('universe').tool_whitelist_names); print(len(r),len(w),sorted(w-r),sorted(r-w))"
# المتوقع: 121 121 [] []
python -m ruff check connectors security hooks
cd universe-agent-ui && npx tsc --noEmit
```
**خطر:** متوسط. **الأعلى خطرًا:** `shell_exec_unsandboxed` — لا تحذف الدالة، احذف التسجيل فقط.

---

## 5. P2 — حذف السطح الميت + Meta (D3)

### 5.1 مسارات الجسر الميتة (بلا مستهلك) — تُحذف

| المسار | app.py | قرار |
|---|---|---|
| `/api/session/{id}` | 1065 | حذف (محجوب بـ`/api/turn/{id}`) |
| `/api/browser/state` | 1695 | حذف |
| `/api/browser/tabs` | 1714 | حذف |
| `/api/indexing/status` | 1759 | حذف (stub يعيد `count:0`) |
| `PATCH /api/projects/{pid}` | 1869 | حذف |
| `DELETE /api/projects/{pid}` | 1897 | حذف |
| `GET /api/agents` | 2337 | حذف (يعيد `[]` دائمًا: `:2347-2348` يتخطى `universe`) |
| `PATCH /api/agents/{name}/model` | 2359 | حذف |
| `GET /api/providers/{name}/keys` | 2761 | حذف (مكرر لـ`GET /api/providers`) |
| `POST /api/voice/check` | 3086 | حذف |
| `GET /api/studio/ave/tools` | 3775 | حذف |
| `POST /api/studio/ave/act` | 3787 | حذف |
| `POST /api/meta/segment` | 3800 | حذف (Meta) |
| `POST /api/meta/test` | 3817 | حذف (Meta) |
| `GET /api/meta/models` | 3830 | حذف (Meta) |
| `POST /api/meta/image` | 3841 | حذف (Meta) |
| `GET /api/studio/design/projects/{pid}/artifacts` | 4067 | حذف |
| `GET /api/studio/design/artifacts/{aid}/preview` | 4079 | حذف |

**يُبقى:** `POST /api/dev/synthesize` (`app.py:801`, dev-only ومبوّب بـ`UNIVERSE_DEV_SIM=1`) · `POST /api/indexing/resync` (حيّ من `Settings.tsx:3144` — **يجب أن يصرّح أنه no-op** بدل `{"indexed":0}`).

### 5.2 مسارات `/api/goal*` — تُحذف مع محرك goal (المرحلة P4، لا هنا)
`app.py:1142,1205,1289,1530,1556,2065,2095,2126,2198` (9 aliases) + `GET /api/goals:1090`. **ملاحظة:** `GET /api/goals` هي قائمة المهام العامة؛ القرار: **تُحذف** مع انتقال الحاوية إلى `sessions` (P4-12)، وتُستبدل بـ`GET /api/sessions` كمرجع وحيد.

### 5.3 سطح Meta — القرار الدقيق

| العنصر | القرار | السبب |
|---|---|---|
| `providers/catalog.py:157-169` (مزوّد Meta كـLLM preset) | **يبقى** | مزوّد OpenAI-compatible عادي، ليس "أداة Meta"؛ ويغذّي `resolve_key` |
| `providers/meta_media.py:131-155` (`transcribe`) + `:47-66` (`resolve_key`) + `MetaAPIError` + `api_base` | **يبقى** | حيّ: `universe_desktop_bridge/voice.py:109-124` و`bots/runtime.py:28` و`botsApi.ts:268` |
| `providers/meta_media.py:97-128` (`generate_image`) | **يُحذف** | مستهلكه الوحيد `/api/meta/image` (ميت) |
| `providers/meta_media.py:158-211` (`segment`, SAM 3.1) | **يُحذف** | مستهلكه الوحيد `/api/meta/segment` (ميت) |
| `providers/meta_media.py:73-94` (`list_models`) + `:31` (`_LLM_MODELS`) | **يُحذف** | مستهلكاه `/api/meta/test` و`/api/meta/models` (ميتان) |
| `Settings.tsx:1646-1667, 1703-1705` | **يُعاد** | الخلفية تُثبّت `stt_provider:"meta"` وتقول "Meta is the ONE speech provider" — **استبدلها بمُحدِّد حقيقي** (local/meta/openai/groq — الكود موجود في `voice.py:100-165`) يعرض المتاح فعلًا |
| `Bots.tsx:1159` + `botsApi.ts:259` | **يُعاد** | "Speech → text" بدل تسمية الموديل |
| `providers/catalog.py:161-168` | **يُعاد** | احذف ذكر Muse Image وSAM 3.1 |

**قرار صريح:** Because the voice channel *works* today via Meta STT، لا نحذفه (يكسر ميزة تعمل). ما نصلحه هو **الارتباط بالهاكاثون**: اختيار المزوّد يصبح صريحًا بدل تثبيته، والادعاءات التوثيقية تصحح. **إن أراد المستخدم Meta-free تمامًا** فالخطوة هي: `Settings.tsx:1652` → `"local"` + التأكد من تثبيت `faster-whisper` + fallback صريح عند غيابه — قرار منفصل يُنفَّذ بعد P7.

### 5.4 الـ11 helper الميتة في العميل

`client/src/lib/api.ts:900-903` (`getProviderKeys`), `921-925` (`checkVoiceKey`), `977-987` (`patchProject`), `989-995` (`deleteProject`), `1146-1149` (`getIndexingStatus`), `1155-1160` (`getBrowserState`), `1166-1170` (`browserTabs`) — حذف.
`client/src/lib/studioApi.ts:183-195` (`generateImage`), `213-227` (`listAveTools`, `driveAve` + نوعا `AveTool`, `AveDriveResult`), `231-239` (`segmentImage`) + `ImageResult:38` + `pickImage/asImage:126-…` + bullet التوثيق `:7-8` — حذف.

### 5.5 وثائق الهاكاثون

- `docs/SUBMISSION_META_HACKATHON.md` — **حذف الملف كاملًا** (92 سطرًا؛ وثيقة تقديم مسابقة).
- `README.md:14` (سطر "🏆 Meta Global AI Developer Hackathon submission") — **حذف**.
- `README.md:20,22,26-31,33-43,45-51,59,109,124,128` — إعادة صياغة per table P7 (يُنفَّذ في P7 لتفادي تشتيت).
- `docs/SKILLS.md` (**جديد**) — يوثّق حالة "كتالوج فارغ" الصادقة (يُنشأ في P5 مع B3).

**تحقق:**
```
git grep -n --untracked -E 'generate_image|meta_media\.segment|list_models|/api/meta/(image|segment|test|models)' -- .   # لا شيء
git grep -n --untracked -E 'getProviderKeys|checkVoiceKey|patchProject|deleteProject|getIndexingStatus|getBrowserState|browserTabs|generateImage|listAveTools|driveAve|segmentImage' -- universe-agent-ui/client/src   # لا شيء
python -c "import universe_desktop_bridge.app; print('bridge imports OK')"
```
**خطر:** متوسط-عالٍ (`app.py` 4200 سطر؛ حذف decorator مشاركة لا يحذف المعالج).

---

## 6. P3 — إصلاحات الـ Agent Loop (A1..A9)

كلها في `core/agent/turn_engine.py` إلا ما يُذكر.

| # | العطل | الدليل | التعديل | اختبار جديد |
|---|---|---|---|---|
| **A1** | `turn_control` كود ميت | `:639` يقرأ `r.get("turn_control")` من قاموس `_run_one` (`:776-781`) الذي مفاتيحه `{call_id,name,ok,raw,llm_content}` | (أ) في `_run_one`: `raw.get("turn_control")` يُرفع إلى مستوى النتيجة؛ (ب) `connectors/builtin/plan_mode.py:29-31` — أداة `ExitPlanMode` تُصدر `{"turn_control":{"stop_after_result":true,"reason":...}}`؛ (ج) `turn_control_stop` يصير قابلاً للإطلاق | `tests/test_turn_control.py::test_tool_turn_control_stops_the_turn` + `test_ask_user_does_not_stop_the_turn` |
| **A2** | حارس إعادة التعبئة ميت | `reset():228-230` بلا مستدعٍ؛ `else:247-248` يصفّر للأبد؛ `:579` يعدّ الدفعات لا الخطوات | نداء `compact_tracking.reset()` بعد نجاح الضغط (`:412`)، والعدّ بخطوات الموديل لا الدفعات | `tests/test_turn_compaction_breaker.py` (3 اختبارات) |
| **A3** | كاشف التكرار لا يُصفَّر عند النجاح | `:566-570` = `pass` | حذف `repeated_calls[signature]` عند نجاح أي استدعاء داخل `_run_one` (`:769-774`) | `tests/test_engine_fix_engine.py::test_duplicate_detector_resets_on_success` |
| **A4** | التزامن معطّل | `READ_CAPABILITIES:160-162` أسماء أدوات vs قدرات حقيقية (`file.read`/`network.req`/`memory.read`) | اشتقاق `readOnly`/`mutating`/`concurrentSafe` من `Tool.capability` الحقيقي؛ تصحيح `sideEffectScope:175-180`؛ **سقف تزامن** (مثل 4) على `gather:783`؛ `dependsOn:183` يبقى `[]` مع توثيق صريح | `tests/test_turn_scheduler.py` (4 اختبارات) |
| **A5** | نتائج الأدوات لا تعبر الأدوار | `session_loop.py:255-262` يخزّن `tool_trace` بـ`result_preview[:300]` | تخزين صفوف `role:"tool"` مع `tool_call_id` (المخزن جاهز: `core/session/store.py:82`)، وإسقاط تركيب `tool_trace`؛ index اختياري في `v0032` | `tests/test_session_tool_persistence.py` (2) |
| **A6** | نافذة السياق 32k ثابتة | `:275` vs `:471`؛ `security/capability.py:207-214` تخمّن بالنص | (أ) `context_window_for_model` يُرجع `int` لا tuple (`context/compaction.py:97-102`); (ب) تمرير النافذة الحقيقية من `session_loop.py:223-229`؛ (ج) `_context_window_for` يستخدم حقل catalog بدل تخمين | `tests/test_context_window.py` (3) |
| **A7** | لا usage في البث | `stream_options` = 0 نتائج | `stream_options={"include_usage": True}` عند دعم المزوّد، وقراءة `chunk.usage` من آخر chunk، وتسجيل التوكنات الحقيقية في `llm_calls` (`client.py:603-611`) | `tests/test_provider_usage.py` (2) |
| **A8** | رد فارغ ينهي الدور صامتًا | `:479,523-529` ⇒ `stop_reason="no_tool_calls"` ونص ""؛ `session_loop.py:294` ⇒ `success=False` بلا خطأ | إعادة سؤال محدودة (2-3 محاولات) ثم `stop_reason="model_empty_reply"` + حدث | `tests/test_turn_empty_reply.py` (2) |
| **A9** | إلغاء يُهمل نتائج | `gather:783` بلا `return_exceptions`; `CancelledError` يُعاد رفعه `:759-760` | `return_exceptions=True` + تحويل `CancelledError` إلى نتيجة `{"ok":False}` + commit لكل معرّف | `tests/test_turn_batch_cancel.py` (1) |

**تحقق:** `pytest tests/test_turn_control.py tests/test_turn_scheduler.py tests/test_turn_compaction_breaker.py tests/test_turn_empty_reply.py tests/test_turn_batch_cancel.py tests/test_context_window.py tests/test_provider_usage.py tests/test_session_tool_persistence.py -q`
**خطر:** متوسط. **تراجع لكل بند:** مُدرج في الملحق 8.

---

## 7. P4 — حذف محرك goal نهائيًا (D2)

**المبدأ:** لا نحذف قبل أن ننقل ما هو ذو قيمة. الترتيب: تهيئة (flag) → هجرة → تعطيل → حذف.

### 7.1 تهيئة (P4-0)
`core/agent/universe_agent.py:320-328`: `GAX_ENGINE` صار inert — `engine_mode` يُقرأ للـ logs فقط، والمسار دائمًا session. الهدف: إيقاف السلوك الميت مبكرًا مع بقاء الكود قابلًا للمراجعة.

### 7.2 الهجرة (P4-1..P4-6) — من goal إلى session

| # | ما يُنقل | من | إلى | ملاحظة |
|---|---|---|---|---|
| M1 | `provider_halt` | `universe_agent.py:140-188` (يستدعى `:664,:674`) | `core/engine/session_loop.py:230-246` | يُصدر `provider_halt` + `turn_failed` **مباشرة على الباص** (لا عبر `timeline.record` — `observability/timeline.py:33-34` يكتم المرآة)، ويكتب `turn_end status=failed`، ويعيد `halted=True` بدل الرمي. **لا يكتب** صف `goals` ولا checkpoint |
| M2 | `project_root_ctx` | `universe_agent.py:452-453` (goal فقط) | `session_loop.py:146` (set عند الدخول، reset في `finally`) | **أهم بند أمني في الخطة**: بدونه البوابة تستخدم الجذر العام بينما البرومبت يقول جذر المشروع |
| M3 | استدعاء L3/L4 | `universe_agent.py:426-430` | `universe_agent.py:273-276` (المسار الافتراضي) | `await asyncio.to_thread(self.memory.search_l4/search_l3)` + `l3.touch` لل(top) |
| M4 | دورة الذاكرة الساخنة | `universe_agent.py:386-393, 576-583, 608-610` | **لا تُنقل** | العقد معرّف في `session_loop.py:13-14` ومثبَّت في `tests/test_session_reflection.py:91-113`: الكتابة ليست على المسار الساخن. الكتابة في P5 عبر حلقة subconscious |
| M5 | `resume_turn` | `universe_agent.py:863-1000` (يرفع "planet not found" دائمًا) | `core/engine/resume.py` (جديد) | إعادة تشغيل من **السجل** (`core/session/store.py:53 transcript()`)، لا من كوكب |
| M6 | `evaluate_goal` + قاعدة الصدق | `core/acceptance.py:90-157` + `_apply_acceptance` (`universe_agent.py:1004-1034`) | `session_loop.py:272-276` | **لا تُحذف**:窮 promising + مُختبَرة (`tests/test_honesty_fixes.py:140-150`). "قبول ناجح لا يحوّل فشلًا إلى نجاح" يبقى كما هو |
| M7 | الإلغاء | goal path `:640-657` | `session_loop.py` | `asyncio.CancelledError` هو `BaseException` ⇒ `except Exception` لا يلتقطه ⇒ اليوم الإلغاء **لا يكتب `turn_end`**. يُضاف فرع صريح |
| M8 | `is_goal_active` | `universe_agent.py:1079-1083` | `is_turn_active` (+ alias) | المسار الافتراضي لا يسجّل `turn_id` في `_active_turn_ids` ⇒ `is_goal_active()` **False دائمًا** اليوم ⇒ حلقة subconscious لا تُفسح المجال |
| M9 | كتابة `goals` | `:331-348` | **تُحذف** | الجدول يبقى كحاوية مهام حتى P4-12 |
| M10 | `classification` | `:381-382` | موجود أصلًا في `session_loop.py:292-293` | لا تغيير (يُستخدم كمدخل لـM6) |
| M11 | تنظيف نهاية الدور | `:701-714` | `session_loop.py` (finally) | `steer.clear` + `swarm.clear` + `hooks.clear_goal_state` — **واليوم这三 يتسرّبون كل دور** |
| M12 | `_load_turn_history` | `:731-784` | **يُحذف** | session path يعيد السجل كاملًا (`session_loop.py:213`) |
| M14 | قفل الدور | `:349-359, 715-729` | `universe_agent.py` `_run_session_turn` | اليوم دوران متزامنان على نفس session يتشابكان في الكتابة |

### 7.3 الحذف (P4-7..P4-11)

1. `core/agent/universe_agent.py:329-729` — **حذف جسم محرك goal كاملًا**.
2. `:315-328` — حذف `GAX_ENGINE` ومفتاح `import os as _os_engine`؛ `run_turn` ينادي `_run_session_turn` بلا شرط.
3. `:848-861` `_mark_goal_failed`/`_mark_goal_cancelled` — حذف (استبدلها صفوف سجل).
4. `:863-1000` `resume_turn` — **إعادة كتابة** (M5).
5. `:140-188` `record_provider_halt` — **إعادة كتابة** (سجل بدل `goals`+checkpoint). **تبقى** وإعادة تصديرها من `core/agent/orchestrator.py:6`.
6. `observability/timeline.py:37-48` `_WIRE` — احذف `"classified":38`، `"obstacle_detected":41`، `"goal_complete":44` (صفر منتج) ونسخة `turn_failed` المكرّرة `:47`؛ أبقِ `agent_start/agent_complete/media_enforcer/turn_complete/turn_failed`، وأعِض `"acceptance_checked"` بحدث حقيقي من M6.
7. `core/agent/swarm.py:177-182` — إزالة `memory.l1.get_planet_by_goal` (لوح المستدعِق) وتمرير التصنيف عبر الاستدعاء.
8. `core/agent/universe_agent.py:1079-1083` — إعادة تسمية (§M8).
9. `scripts/s1d_orchestrator.py` — حذف (سكربت تاريخي يرقّع كودًا غير موجود).
10. `failure/checkpoint.py:65-87` + `cli/wizards/__init__.py:86-103` (`/resume`) + `cli/slash_commands.py:62` — **إعادة كتابة** `/resume` من السجل؛ ثم حذف `checkpoint()`/`load_checkpoint()`/`list_checkpoints()` إن لم يبقَ مستهلك.
11. `cli/wizards/__init__.py:188-262, 568` (`/agents`, `/agent create`) — آلة **Moons** المحذوفة ⇒ **حذف**، مع إزالة تسجيلاتها من `cli/slash_commands.py` و`universe_desktop_bridge/slash.py` (طبقة الأوامر ثلاثية: CLI → bridge → UI) ومن `client/src` إن وُجدت.

### 7.4 الحاوية: `goals` → `sessions` (P4-12) + v0031 (P4-13)

**كل** قراءة/كتابة `goals` في الجسر تُنقل إلى `sessions`:
`app.py:264-275` (sweep), `1017,1036-1037` (dual-read), `1097-1113` (قائمة goals), `1145-1148` (تفاصيل الدور), `1224-1244` (تعبئة الأحداث), `1317-1323` (طابور المتابعة), `1369-1400` (INSERT/UPDATE), `2068,2090` (PATCH), `2100-2112` (DELETE), `2140-2162` (branch), `2176-2177` (compact), `2206-2240` (truncate), `2465-2486` (feedback), `2107,2232` (planets) · `memory_view.py:70-73,88` · `bots/runtime.py:296-305` · `cli/wizards/__init__.py:78,90` · `observability/metrics.py:40-51` · `scripts/e2e_full_path.py:64,107,145` · `tests/e2e/test_session_e2e.py:184-188` (بذر) · `universe-agent-ui/client/tests/e2e/sidebar_workspace.py:33-72` (بذر).

**`schema/v0031_drop_goal_engine.py` (جديد)** — الترتيب حاسم:
```sql
-- (1) إصلاح ثغرة v30: إنشاء turn_feedback
CREATE TABLE IF NOT EXISTS turn_feedback (turn_id TEXT NOT NULL, rating TEXT NOT NULL, ts REAL NOT NULL);
INSERT OR IGNORE INTO turn_feedback(turn_id, rating, ts) SELECT turn_id, rating, ts FROM goal_feedback;
DROP VIEW IF EXISTS goal_feedback;  CREATE VIEW IF NOT EXISTS goal_feedback AS SELECT * FROM turn_feedback;

-- (2) sessions تستوعب حاوية المهام
ALTER TABLE sessions ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE sessions ADD COLUMN text TEXT NOT NULL DEFAULT '';
ALTER TABLE sessions ADD COLUMN final_summary TEXT;
ALTER TABLE sessions ADD COLUMN completed_at REAL;
ALTER TABLE sessions ADD COLUMN last_turn_at REAL;
INSERT OR IGNORE INTO sessions(...) SELECT ... FROM goals WHERE kind='task';

-- (3) جداول حقبة goal
DROP TABLE IF EXISTS handoffs; DROP TABLE IF EXISTS planets; DROP TABLE IF EXISTS moons;

-- (4) goals أخيرًا
DROP VIEW IF EXISTS tasks; DROP VIEW IF EXISTS turns; DROP TABLE IF EXISTS goals;
```
- **لا تُحذف**: `asteroids`, `task_outcomes`, `skill_activations`, `capability_grants`, `tool_lifecycle`, `stars`, `star_edges`, `skills`, `rules`, `orbits`, `dark_matter`, `sessions` (محرّكات مستقلة عن goal).
- **الأمان:** عدّاد الـ migration يشغّل `integrity_check` قبل/بعد، نسخة احتياطية، معاملة واحدة، واستعادة عند الفشل (`schema/migrations.py:73-140`) ⇒ القواعد existentes آمنة.
- **شرط سابق:** تشغيل `scripts/migrate_goals_to_sessions.py` حتى الاكتمال (idempotent) والتأكد أن كل صف `kind='message'` له ما يقابله في السجل؛ يوصى بأرشفة `CREATE TABLE goal_archive AS SELECT * FROM goals` قبل الحذف.

### 7.5 إعادة كتابة الاختبارات (P4-14)

| الاختبار | القرار |
|---|---|
| `tests/e2e/test_session_e2e.py:155-164` `test_goal_engine_still_available_via_flag` | **حذف** → استبدله `test_goal_engine_env_var_is_ignored` (المتغير صار عديم الأثر) |
| `tests/test_audit_fixes.py:314-353` | **إعادة كتابة** → `test_cancelled_session_turn_records_cancelled_not_failed` (سجل `turn_end status=cancelled`، لا صف goals، لا checkpoint) |
| `tests/test_m5_bridge_contracts.py:58-80` | **إعادة كتابة** → `test_session_turn_carries_project_meta_in_the_transcript` |
| `tests/test_m5_bridge_contracts.py:83-103` | **إعادة كتابة** → `test_provider_halt_single_choke_point_session` (حدث الباص + صف `turn_end` + `halted is True` + لا goals + لا checkpoint) |
| `tests/test_m5_bridge_contracts.py:106-144` | **إعادة كتابة** على `sessions` |
| `tests/test_provider_routing_e2e.py:218-240` | **إعادة كتابة** → raw error + `kind="model_unavailable"` في الحدث |
| `tests/test_provider_routing_e2e.py:402-415` | **إعادة كتابة** → سجل `turn_end status=cancelled` |
| `tests/test_ui_overhaul.py:143-157` | **إعادة كتابة** (بلا تأكيد كوكب) |
| `tests/test_schema.py:245-283` | **إعادة كتابة** (فهرس `idx_planets_goal` يزول) |
| `tests/test_task_threading.py` (كله) | **إعادة كتابة** على `sessions` |
| `tests/test_failure_fixes.py:105-118` | **إعادة كتابة** على `sessions` |
| `tests/test_goal_mode_v22.py` | **يبقى** (كل اختباراته تثبت **غياب** goal mode) — يُعاد تسميته `tests/test_agent_registry_v22.py` |
| `tests/test_session_reflection.py:91-113` | **يبقى** — لا تلمسه (عقد "لا كتابة ذاكرة على المسار الساخن") |
| `tests/test_e2e/test_cli_subprocess_e2e.py:28` | **حذف السطر** |

### 7.6 لا يُحذف (قائمة الأمان)
`core/acceptance.py` ومقتضيات الصدق · `_build_turn_messages:786-845` · `record_provider_halt` (مُعاد كتابته) · `core/agent/project_context.py:6` · `core/agent/steer.py:43` + `core/agent/swarm.py:54` + `hooks/manager.py:151` (تُستدعى الآن من session) · `core/memory/__init__.py:193-206` + `l2` forgetting/promote · `core/memory/layers/l3_semantic.py` + كاتب reflection · `observability/tool_lifecycle.py` + `events.bind_goal` · `security/capability.py:396-433` · `schema/v0027_sessions.py` + `core/session/store.py` · `scripts/migrate_goals_to_sessions.py` · `failure/checkpoint.py` (حتى P4-10) · `core/memory/layers/l1_working.py` + `complete_task` (اختياري: تُنقل أو تُقبل كطبقة خاملة) · `observability/timeline.py` mappings الحيّة · `tests/test_goal_mode_v22.py`.

**تحقق:** `pytest tests/e2e/test_session_e2e.py tests/test_schema.py tests/test_desktop_bridge.py tests/test_task_threading.py tests/test_m5_bridge_contracts.py tests/test_provider_routing_e2e.py tests/test_audit_fixes.py tests/test_ui_overhaul.py -q` ثم المجموعة الكاملة.
**خطر:** **عالٍ** (أكبر تعديل في المشروع). **تراجع:** نسخة DB الاحتياطية + استعادة `git` + `schema/migrations.py` يستعيد القاعدة تلقائيًا.

---

## 8. P5 — الذاكرة حيّة + المجرة ثلاثية الأبعاد (D4)

| # | البند | التفصيل | التحقق |
|---|---|---|---|
| **B1** | تشخيص | المعطّل: استدعاء فارغ (`:273`)، كاتب واحد (subconscious) يبدأ من CLI فقط (`cli/repl.py:66-67`)، `app.py` بلا subconscious | — |
| **B2a** | تشغيل الحلقة في الجسر | `universe_desktop_bridge/app.py` block الـ lifespan (~`:296-315`): `subconscious = get_loop(); subconscious.start()` + إيقافه عند الإغلاق | `tests/test_desktop_memory_wiring.py::test_bridge_lifespan_starts_the_subconscious_loop` |
| **B2b** | كتابة الذاكرة | حلق�� subconscious تكتب L3 (`:175`)؛ نضيف كتابة L2 (asteroid) في نهاية الدور **خارج المسار الساخن** (`to_thread`)؛ L5 `record_outcome` اختياري | `test_session_turn_writes_an_l2_asteroid` |
| **B2c** | الاستدعاء | `universe_agent.py:273-276` → `search_l4(top_k=6)` + `search_l3(top_k=5)` + `l3.touch` (كما في goal path `:426-430`) | `test_session_prompt_carries_l3_stars` |
| **B2d** | تسجيل الدور النشط | `_active_turn_ids.add(turn_id)` في session path ⇒ `is_turn_active()` صحيح ⇒ حلقة subconscious تُفسح المجال | `test_turn_active_is_true_during_a_session_turn` |
| **B3** | كتالوج المهارات | `skills_data/` غير موجود؛ `skills/loader.py` stub؛ `skill/ingestion.py:351` بلا مستدعٍ ⇒ **لا يمكن إنتاج محتوى**. القرار: آلة حيّة + كتالوج فارغ موثَّق: `docs/SKILLS.md` جديد + `pyproject.toml:98` إزالة `"skills_data"` + `tests/test_skills_corpus.py` | `pytest tests/test_skills_corpus.py -q` |
| **B4a** | عقد المجرة | `universe_desktop_bridge/universe_view.py` (جديد): `universe_snapshot()` → `{generated_at, counts, systems[], stars[], skillsByOrbit{}, planets[], asteroids[], memories[], empty}` — نفس شكل `RealUniverseMemory` في `client/src/lib/universe/generate.ts:407-415` + طبقات L1/L2/L3 | `tests/test_universe_view.py::test_universe_snapshot_matches_the_client_contract` |
| **B4b** | المسار | `GET /api/memory/universe` بجوار `app.py:703-705` | `test_bridge_exposes_the_universe_endpoint` |
| **B4c** | العميل | `client/src/stores/universeMemory.ts` (جديد) + `api.universeMemory()` + تمرير `real` إلى `EmptyState.tsx:21` و`TrailingUniverse.tsx:36` (بدل `null`) | `npx tsc --noEmit` |
| **B4d** | النجوم الحقيقية في وضع orb | `generate.ts:610` يربط النجوم الحقيقية بـ`starBases` المحصورة بـ`P.stars` (و`orb` = 0) ⇒ **النجوم الحقيقية لا تظهر أبدًا**؛ إضافة حقن `starBases` من `realStars` عند `starBases.length === 0` | لقطة حيّة للمجرة بعد جولة ذكرى |
| **B4e** | ضمان عدم الكسر | `buildUniverse(q, null)` يبقى **بصمت متطابق** (كل الفروع محروسة بـ`real` غير فارغ) | لقطة قبل/بعد |

**خطر:** متوسط (B2b يمسّ مسار الدور). **تراجع:** إزالة block الكتابة ⇒ الذاكرة خاملة (حالة ما قبل الإصلاح).

---

## 9. P6 — عقد الجسر/الواجهة

| # | البند | التفصيل |
|---|---|---|
| P6-1 | **12 حدثًا تُصدر ولا تُعرض** | `provider_retry`, `model_complete`, `llm_max_tokens_fallback`, `memory_updated`, `skill_searched`, `skill_read`, `task_ledger`, `task_stop_requested`, `agent_message`, `catalog_probes_updated`, `audit_chain_broken`, `turn_start` — أضِف cases في `client/src/lib/flow/fold.ts:259+` و`ztimeline.ts:410+`؛ واحذف تكرار `turn_start` من `observability/timeline.py:49` |
| P6-2 | **3 أحداث تُعرض ولا تُصدر** | `turn_planned`, `turn_replanned`, `handoff` (`fold.ts:263,541,496`، `ztimeline.ts:417,418,711`، `Agents.tsx:173`) — إمّا تُصدر من `universe_agent.py` (plan/replan/handoff) أو تُحذف الفروع. **قرار: تُحذف** (المحرك لا يخطّط/يعيد خطّة) وتُحذف من `Agents.tsx` |
| P6-3 | **لا مسار 401/403** | `auth.py:145,152,172` ترفض؛ `api.ts:155-170` لا يميّز ⇒ WS 1008 ⇒ offline بلا زر Retry (`OfflineBanner.tsx:18`). الإصلاح: في `api.ts` عند `status===401` أعد جلب التوكن عبر `preload.bridgeInfo()` وأعد المحاولة مرة؛ وفي `ws.ts:105` اعتبر 1008 = `failed` لا `offline` |
| P6-4 | **Electron بجسر ميت** | `main.ts:205-209` (خطأ spawn بلا إعادة تشغيل) · `:167-173` (الجسر Retry لا يعمل) · `:285` (جسر معلّق) · `:267` (يبقى "starting" ⇒ لا زر) — الإصلاح: جدولة إعادة تشغيل على حدث `error`؛ تجاوز حارس packaged-backend في `universe:bridge-restart`؛ إعادة التشغيل بعد N فشل صحي متتالية؛ توسيع نمط زر Retry ليشمل "starting"/"down" |
| P6-5 | **11+ إعدادًا ميتة** | `app.py:562-590` + `:3001-3061` + `api.ts:300-328` + `Settings.tsx` — إما وصل كل مفتاح بمستهلك أو حذفه من النموذج والواجهة؛ `browserHeadless` (`app.py:586`) يجب أن يقرأه `connectors/builtin/browser.py:84` |
| P6-6 | **ازدواج `_find_muse`** | `app.py:3419` (داخلي: glob) vs `:3456` (وحدة: PATH + POSIX) — دمج في دالة واحدة، أو حذف التثبيت التلقائي لـ`curl … install.sh` (تنفيذ سكربت بعيد!) إذا لم يكن مطلوبًا |
| P6-7 | **TTL في وصف الأداة** | `connectors/builtin/computer_use.py:236` يقول "default 45s" والكود 15 (`universe_cua_helper.py:48,53`) — تصحيح النص |
| P6-8 | **`encodeURIComponent` ناقص** | `api.ts:646,694` تبني المعرّف بلا ترميز ⇒ معرّف فيه `/` يصيب مسارًا آخر — إضافة `encodeURIComponent` |
| P6-9 | **`/api/indexing/resync` يكذب** | `app.py:1765-1767` يعيد `{"indexed":0}` ويقول "queued" — صرّح أنه no-op أو نفّذه |
| P6-10 | **الأحداث حرّة اللون** | `observability/timeline.py:33-34` يكتم مرآة `chat_reply`/`provider_halt` ⇒ `provider_halt` يجب أن يُصدر **مباشرة** على الباص (مربوط بـM1) |

**تحقق:** `pytest tests/test_desktop_bridge.py -q` + فحص حيّ للخط الزمني (حدث إعادة محاولة ظاهر) + `npx tsc --noEmit`.

---

## 10. P7 — تصحيح التوثيق (بلا استثناء)

### 10.1 README (يُحرَّر بعد P1/P2 لأن الأرقام تتغيّر)
| السطر | الحالي | البديل |
|---|---|---|
| 10 | "powered by Meta's latest Muse models …" | "runs on any OpenAI-compatible provider; Meta's Muse models are one preset in Settings → Providers" |
| 14 | سطر "🏆 Meta Global AI Developer Hackathon submission" | **حذف** |
| 16 | "calls 132 real tools" · "runs end-to-end on Meta's Model API — and the bots literally run on Muse Code" | "calls **121** real tools" · "runs on any installed provider; **Muse Code** is an optional external bots runtime" |
| 20-43 | قسم "Where Meta's models are used" + "Where do I put the Meta API key?" | "Providers and optional integrations" · "Where do I put a provider key?" + **تصحيح خاطئ "المفتاح كافٍ للشات"**: سلسلة الشات تقرأ من الخزنة المحلية (`providers/manager.py` لا يقرأ `os.environ`)، فالمفتاح البيئي وحده لا يكفي للشات |
| 28, 30 | صفّا "Image generation (Muse Image)" و"Media segmentation (SAM 3.1)" | **حذف** (الواجهة لا وجود لها) |
| 45-51 | "Muse Code — the bots runtime" | "— an optional bots runtime" + ذكر الـfallback الصادق |
| 59 | "with inline **Muse Image** generation" | احذف البند |
| 65 | "Five memory tiers … distill every session" | نفس + **نطاق صادق**: "الطبقة الإجرائية تشحن بم catalog فارغ ( purge 2026-10-31 ) — انظر docs/SKILLS.md" + عتبة الترقية (0.45 idle / 0.85 real-time، `config.py:114-120`) |
| 100-101 | "Linux ✅ Verified and tested" / "Windows ⚠️ Not tested" | ما يثبته CI فعلًا: كلاهما CI-verified؛ ووسيط computer-use **Windows-only** (`universe_cua_helper.py:61-62`) — فالعكس منطقيًا |
| 109 | "the **Meta provider** (… Muse Image / Muse Voice Transcribe / SAM 3.1)" | "catalog includes a **Meta** preset (`api.meta.ai/v1`, Muse Wonder / Muse Spark) alongside the others" |
| 110 | "132 registered tools" | "**121**" |
| 118 | "5 specialist swarm roles … up to 8 missions per goal" | "the `Agent` tool spawns **worker** missions — unspecialised, sharing the toolset — depth 2, 4 per call, 8 per turn" (`swarm.py:76-78,120`) |
| 122 | "609 tests passing / 0 failing … live bridge smoke (`scripts/bridge_smoke_p0.py`)" | **<N> مقيس من CI**؛ وحذف ذكر السكربت (لم يوجد في تاريخ git) |

### 10.2 بقية الوثائق
| الملف:السطر | الإجراء |
|---|---|
| `docs/SECURITY.md:178` | إعادة صياغة الصف: لا `security/windows_sandbox.py` ولا `docs/WINDOWS_SANDBOX.md`؛ الشيفرة مبنية-for-reference فقط في `vendor/codex-windows-sandbox/` |
| `docs/SECURITY.md:179` | "18 trusted sources" → "the corpus is **empty** (`TRUSTED_SKILL_SOURCES == ()`)" |
| `docs/SECURITY.md:206-208, 266-271` | `http.egress` → `network.req`; "White Hole لكل جلسة" → صف `capability_grants` + "once" أحادي الاستخدام + توضيح أن White Hole قاعدة دائمة |
| `docs/UI-OVERHAUL.md:91,125,162,188,260-261` | `MiniUniverse.tsx` → `MemorySphere.tsx`; W3 `/welcome` → **NOT IMPLEMENTED**؛ "12 agent cards" → **فارغة** (وكيل واحد)؛ `ActivityFeed.tsx` → `ActivityPane.tsx`؛ "definition of done" → أضف حالة 2026-11 |
| `docs/UI-STATUS-MASTER-SPEC.md:5` | وسم صريح أن الملفات المشار إليها غير موجودة |
| `docs/parity.md:10,13,57,71,90,98,131,139` | إزالة مراجع الملفات المفقودة؛ صفّا "أوضاع الصلاحيات" يذكر 4 لا 5؛ صفّ "Subagent roster فارغ"؛ صفّ "Exit plan mode ينهي الدور" بعد A1 |
| `docs/failure-diagnosis.md:141` | الأدلة ليست في المستودع |
| `docs/events-v1.md:66,105,127,146` | تصحيح مصدر الأحداث (`turn_engine.py:438/471`، `universe_agent.py:162`) + **سجل إضافات** لأحداث 2026-11-10 (`model_empty_reply`, `turn_control_stop`) |
| `docs/STRUCTURE_REVIEW.md` + `.ar.md` | **لا تُحرَّر الأرقام** (تقرير مؤرَّخ) — يُضاف شريط "SUPERSEDED 2026-11-10" يشرح: حذف الـ14، عدد 121، سطح Meta الميتى، حذف aliases، إعادة تسمية `TurnOrchestrator`، إصلاح hook coverage |
| `vendor/codex-windows-sandbox/README.md:33,39,47,50` | "There is no `security/windows_sandbox.py` …" + "Galaxy" → "this repo" |
| `pyproject.toml:98` | إزالة `"skills_data"` من حزمة wheel (المجلد غير موجود) |
| `docs/SKILLS.md` (**جديد**) | الحالة الصادقة لكتالوج المهارات |
| `.gitignore` | `payload.txt`, `debug.log`, `pw_probe_*.js`, `.vite_manual.log`, `pytest_*.log`, `tmp_screens/`, `.tooltest/`, root `package*.json`, `shot.png` |
| حذف `docs/SUBMISSION_META_HACKATHON.md` | (P2) |

**تحقق:**
```
git grep -ni --untracked -E '132 tools|132 registered|609 tests|bridge_smoke_p0|http\.egress|WINDOWS_SANDBOX|MiniUniverse|ActivityFeed|Muse Image|SAM 3|hackathon|shell-re-map|shell-tokens|shell-live-screens' -- README.md docs/ vendor/ pyproject.toml
# المتبقي فقط: شريط STRUCTURE_REVIEW
```

---

## 11. P8 — التحقق النهائي

1. `pytest -m "not llm" -q` (كامل، بلا `-x`) ⇒ أخضر؛ `pytest -m "not llm" --collect-only -q | tail -1` ⇒ العدد الحقيقي → يُكتب في README.
2. `python -m ruff check core connectors security hooks providers universe_desktop_bridge cli schema tests`.
3. `python -m compileall -q core connectors security hooks providers universe_desktop_bridge cli`.
4. `cd universe-agent-ui && npx tsc --noEmit && npm run check && npm run build`.
5. **فحص حي** (كما في جلسة التشغيل): تشغيل vite+electron+bridge، ثم:
   - لقطة نافذة "Universe Agent — Desktop" ⇒ الواجهة تحمّل.
   - دور حقيقي ⇒ الخط الزمني يعرض أدوات و`provider_retry` (P6-1) بعد إجباره.
   - `/api/memory/universe` ⇒ `empty:false` بعد دور يكتب ذاكرة، والمجرة ثلاثية الأبعاد ترسم نجومًا حقيقية.
   - `POST /api/feedback` ⇒ 200 على قاعدة بيانات جديدة (بعد v31).
6. `git grep` على قائمة "لا يُحذف" (§7.6) للتأكد من بقائها.

---

## 12. سجل القرارات

| القرار | السبب |
|---|---|
| الـ14 تُحذف لا تُدرج | طلب المستخدم D1 + invariant `registry == whitelist` يمنع عودة اليتيم |
| goal يُحذف بهجرة أولًا | أجزاء منه حيّة (project_root_ctx، provider_halt، acceptance، ذاكرة) — حذفها مباشرة يكسر الميزات |
| `goals` تبقى حاوية حتى P4-12 | الجسر مبني عليها في 15 موضعًا؛ النقل دفعة واحدة مع migration |
| مزوّد Meta يبقى كـ preset | ليس "أداة Meta"؛ يخدم LLM + هو مفتاح Muse Code + STT حيّ |
| `meta_media.transcribe` يبقى | حيّ في مسار الصوت والـBots؛ حذفه يكسر ميزة تعمل |
| STT الافتراضي | نُبقي Meta افتراضيًا الآن ونعزله عن الهاكاثون في الواجهة/التوثيق؛ تحويله لـlocal قرار منفصل |
| المجرة تبقى | طلب المستخدم D4 + B4 يربطها ببيانات حقيقية مع fallback إجرائي |
| `docs/STRUCTURE_REVIEW.*` لا تُحرَّر | تقرير مؤرَّخ — التزوير أسوأ من الركود؛ شريط SUPERSEDED يكفي |
| `GET /api/goals` تُحذف | لا مستهلك؛ `/api/sessions` هي المرجع بعد P4-12 |

---

## 13. مخاطر واستراتيجيات

| الخطر | الأثر | التخفيف |
|---|---|---|
| `shell_exec_unsandboxed` يُحذف خطأً | `/api/terminal` ينهار | فحص صريح: `git grep "shell_exec_unsandboxed"` ⇒ يجب أن يبقى `app.py:3245` |
| `meta_media.resolve_key` يُحذف | مسار الصوت والـBots ينهاران | نفس الفحص: `git grep "meta_media"` ⇒ يبقى `resolve_key/transcribe/MetaAPIError` |
| `UniverseAgent` يُعاد تسميته في المكان الخطأ | البوابة تفقد القائمة البيضاء | **المفتاح `"universe"` لا يُلمس**؛ إعادة التسمية في `core/agent/universe_agent.py` فقط، مع إبقاء `Orchestrator` aliases |
| v31 يفقد بيانات | فقدان مهام | `goal_archive` + نسخة احتياطية + `integrity_check` + شرط `migrate_goals_to_sessions.py` |
| تغييرات الذاكرة تكسر عقد "لا كتابة على المسار الساخن" | اختبار `test_session_reflection.py:91-113` يفشل | الكتابة في الخلفية (idle loop / `to_thread`) لا في الدور نفسه |
| الذاكرة تكتب بلا توقف | نمو الجداول | حدود `config.py`Retention + ترقية النجوم |
| الذاكرة لم تُعدّ بالأرقام | README يكذب مجددًا | P8 يعيد القياس؛ **لا رقم مكتوب يدويًا** |
| التطبيق لا يقلع بعد حذف كبير | انقطاع العمل | كل مرحلة مستقلة + commit لكل مرحلة + تراجع لكل بند (§14) |

---

## 14. التراجع لكل بند

| البند | التراجع |
|---|---|
| A1 | حذف سطرَي `turn_control` في `plan_mode.py:29-31` |
| A3 | إعادة `pass` عند `turn_engine.py:566-570` |
| A4 | إرجاع `_classify_tool_call` للمجموعة القديمة (التزامن يعود تسلسليًا) |
| A5 | إرجاع كتلة `tool_trace` في `session_loop.py:255-262` |
| A6 | إرجاع tuple (مع بقاء TypeError) |
| A7 | `supports_stream_usage=False` لكل preset (يعود التقدير) |
| A8 | إزالة فرع `empty_replies` (يعود الإنهاء الصامت) |
| A9 | إزالة `return_exceptions=True` |
| B2 | إزالة كتلة الكتابة والاستدعاء (الذاكرة خاملة) |
| B4 | العميل يمرّر `null` (المجرة الإجرائية) |
| P4 | `git revert` + استعادة القاعدة من `universe.db.bak.<epoch>` |

---

## 15. قائمة "لا يُحذف" (تُراجَع في P8)

`core/acceptance.py` + `_apply_acceptance` · `_build_turn_messages` · `record_provider_halt` · `core/agent/project_context.py` · `steer.clear` + `swarm.clear` + `hooks.clear_goal_state` · `core/memory/__init__.py:193-206` · `core/memory/layers/l3_semantic.py` + كاتب reflection · `observability/tool_lifecycle.py` + `observability/events.py:bind_goal` · `security/capability.py:396-433` · `schema/v0027_sessions.py` + `core/session/store.py` · `scripts/migrate_goals_to_sessions.py` · `shell_exec_unsandboxed` · `providers/meta_media.{resolve_key,transcribe,MetaAPIError,api_base}` · `core/core_agents/agents.py` class `UniverseAgent` + المفتاح `"universe"` · `observability/timeline.py` mappings الحيّة · `tests/test_goal_mode_v22.py` · `tests/test_session_reflection.py:91-113` · `core/memory/layers/l1_working.py` + `complete_task` (اختياري: نقل أو قبول خمول)

---

*نهاية الخطة. التنفيذ يبدأ من P0 — والمطلوب مني: تأكيد أن المستودع نظيف (working tree) وأن لا وكيل آخر يعدّل نفس الملفات أثناء التنفيذ.*
