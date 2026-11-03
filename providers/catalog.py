"""providers/catalog.py — the single source of truth for every provider
Universe Agent can connect to.

Three groups:
- free  : cloud providers with a genuinely free tier (a key is still needed)
- paid  : pay-per-token cloud providers
- local : OpenAI-compatible servers on this machine (no key, no internet)

Every base_url below speaks the OpenAI chat-completions protocol, because
the LLM client (providers/client.py) uses the official openai SDK against
base_url. Suggested models are convenience defaults — the real model list
is discovered live by the bridge's probe when a key is verified.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderPreset:
    name: str
    base_url: str
    category: str  # "free" | "paid" | "local"
    models: list[str] = field(default_factory=list)  # first entry = suggested default
    key_url: str = ""      # where the user obtains a key ("" for local)
    key_optional: bool = False  # local servers accept no key
    # False = this endpoint rejects the OpenAI response_format=json_object
    # transport field (the model can still follow an in-prompt JSON
    # instruction). client.py reads this instead of hardcoding provider names.
    supports_response_format: bool = True
    note: str = ""
    # URL template placeholders the user must fill (e.g. ("account_id",)).
    # Empty = base_url is used as-is. catalog_payload() exposes this so the
    # UI can prompt for the values and send a resolved base_url instead.
    url_fields: tuple[str, ...] = ()


PROVIDER_CATALOG: list[ProviderPreset] = [
    # ---- free-tier cloud ---------------------------------------------------
    ProviderPreset(
        name="Groq",
        base_url="https://api.groq.com/openai/v1",
        category="free",
        models=["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "gemma2-9b-it"],
        key_url="https://console.groq.com/keys",
        note="Very fast inference, generous free tier.",
    ),
    ProviderPreset(
        name="Google AI Studio",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        category="free",
        models=["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro"],
        key_url="https://aistudio.google.com/apikey",
        note="Gemini models through the OpenAI-compatible endpoint.",
    ),
    ProviderPreset(
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        category="free",
        models=["meta-llama/llama-3.3-70b-instruct:free",
                "google/gemini-2.0-flash-exp:free",
                "deepseek/deepseek-chat-v3-0324:free"],
        key_url="https://openrouter.ai/keys",
        note="Aggregates hundreds of models; the ':free' suffix ones cost nothing.",
    ),
    ProviderPreset(
        name="Mistral",
        base_url="https://api.mistral.ai/v1",
        category="free",
        models=["mistral-small-latest", "mistral-medium-latest", "open-mistral-nemo"],
        key_url="https://console.mistral.ai/api-keys",
        note="La Plateforme free tier.",
    ),
    ProviderPreset(
        name="Cerebras",
        base_url="https://api.cerebras.ai/v1",
        category="free",
        models=["llama-3.3-70b", "llama3.1-8b"],
        key_url="https://cloud.cerebras.ai/",
        note="Wafer-scale inference — extremely fast, free daily quota.",
    ),
    ProviderPreset(
        name="NVIDIA NIM",
        base_url="https://integrate.api.nvidia.com/v1",
        category="free",
        models=["nvidia/nemotron-3.5-super-vl-120b-a12b",
                "nvidia/llama-3.1-nemotron-70b-instruct",
                "meta/llama-3.1-70b-instruct"],
        key_url="https://build.nvidia.com/",
        note="Free API credits — VL model needs supports_response_format=False.",
        supports_response_format=False,
    ),
    ProviderPreset(
        name="Kilo Gateway",
        base_url="https://api.kilo.ai/api/gateway",
        category="free",
        models=["dots-studio/dots-3-note-preview:free",
                "nex-agi/nex-n2.5-pro:free",
                "poolside/laguna-s-2.1:free"],
        key_url="https://kilo.ai/",
        supports_response_format=False,
        note="Gateway to many models with free options.",
    ),
    ProviderPreset(
        name="OpenCode Zen",
        base_url="https://opencode.ai/zen/v1",
        category="free",
        # The gateway forbids GET /models (403) — these curated ids are what
        # the routing fallback and Settings dropdowns show. muse-spark-1.3
        # confirmed live by the user; glm/kimi/qwen/minimax are the Zen
        # free lineup (opencode.ai/docs/zen).
        models=["muse-spark-1.3", "glm-4.7", "kimi-k2-0905-preview",
                "qwen3-coder-plus", "minimax-m2"],
        key_url="https://opencode.ai/auth",
        supports_response_format=False,
        note="OpenCode's curated model gateway — free tier with GLM/Kimi/Qwen.",
    ),
    ProviderPreset(
        name="Cloudflare Workers AI",
        base_url="https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
        category="free",
        models=["@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                "@cf/qwen/qwen3-32b",
                "@cf/deepseek/DeepSeek-V3.1-Terminus"],
        key_url="https://dash.cloudflare.com/profile/api-tokens",
        url_fields=("account_id",),
        note="Free daily quota; needs your Cloudflare Account ID in the URL.",
    ),
    ProviderPreset(
        name="GitHub Models",
        base_url="https://models.github.ai/inference",
        category="free",
        models=["openai/gpt-4.1", "meta-llama/Llama-3.3-70B-Instruct",
                "mistral/Mistral-large-2411"],
        key_url="https://github.com/settings/personal-access-tokens",
        note="Fine-grained PAT with the Models scope; free monthly allowance.",
    ),
    ProviderPreset(
        name="SambaNova",
        base_url="https://api.sambanova.ai/v1",
        category="free",
        models=["DeepSeek-V3.2", "Llama-3.3-70B-Instruct", "Qwen3-32B"],
        key_url="https://cloud.sambanova.ai/apis",
        note="Rapid inference, generous free credits.",
    ),
    ProviderPreset(
        name="Featherless",
        base_url="https://api.featherless.ai/v1",
        category="free",
        models=["zai-org/GLM-4.6", "moonshotai/Kimi-K2-Instruct",
                "deepseek-ai/DeepSeek-V3.2-Exp"],
        key_url="https://featherless.ai/settings",
        note="Hosts 10k+ open-weight models, free tier included.",
    ),
    # ---- paid cloud --------------------------------------------------------
    ProviderPreset(
        name="Meta",
        base_url="https://api.meta.ai/v1",
        category="paid",
        # muse-wonder-1.0 is Meta's frontier model (1M ctx); muse-spark-1.3
        # is the fast/cheap tier. Media models (Muse Image, Muse Voice
        # Transcribe, SAM 3.1) are served by providers/meta_media.py, not
        # by chat routing.
        models=["muse-wonder-1.0", "muse-spark-1.3", "muse-spark-1.2"],
        key_url="https://dev.meta.ai/",
        note="Meta Model API — OpenAI-compatible. Also powers Muse Code "
             "(bots runtime), Muse Image and Voice Transcribe.",
    ),
    ProviderPreset(
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        category="paid",
        models=["gpt-4o", "gpt-4o-mini", "o3", "gpt-4.1"],
        key_url="https://platform.openai.com/api-keys",
    ),
    ProviderPreset(
        name="Anthropic",
        base_url="https://api.anthropic.com/v1",
        category="paid",
        models=["claude-sonnet-4-5", "claude-haiku-4-5", "claude-opus-4-1"],
        key_url="https://console.anthropic.com/settings/keys",
        note="Claude models through Anthropic's OpenAI-compatible endpoint.",
    ),
    ProviderPreset(
        name="xAI",
        base_url="https://api.x.ai/v1",
        category="paid",
        models=["grok-4", "grok-3", "grok-3-mini"],
        key_url="https://console.x.ai",
    ),
    ProviderPreset(
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        category="paid",
        models=["deepseek-chat", "deepseek-reasoner"],
        key_url="https://platform.deepseek.com/api_keys",
        note="Very cheap per-token pricing.",
    ),
    ProviderPreset(
        name="Together.ai",
        base_url="https://api.together.xyz/v1",
        category="paid",
        models=["meta-llama/Llama-3.3-70B-Instruct-Turbo",
                "Qwen/Qwen2.5-72B-Instruct-Turbo"],
        key_url="https://api.together.ai/settings/api-keys",
    ),
    ProviderPreset(
        name="Fireworks",
        base_url="https://api.fireworks.ai/inference/v1",
        category="paid",
        models=["accounts/fireworks/models/llama-v3p3-70b-instruct"],
        key_url="https://fireworks.ai/api-keys",
    ),
    ProviderPreset(
        name="Cohere",
        base_url="https://api.cohere.ai/compatibility/v1",
        category="paid",
        models=["command-r-plus-08-2024", "command-r-08-2024"],
        key_url="https://dashboard.cohere.com/api-keys",
        note="Uses Cohere's OpenAI compatibility endpoint.",
    ),
    ProviderPreset(
        name="Perplexity",
        base_url="https://api.perplexity.ai",
        category="paid",
        models=["sonar", "sonar-pro", "sonar-reasoning"],
        key_url="https://www.perplexity.ai/settings/api",
        note="Search-grounded answers.",
    ),
    ProviderPreset(
        name="HuggingFace",
        base_url="https://router.huggingface.co/v1",
        category="paid",
        models=["meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen2.5-72B-Instruct"],
        key_url="https://huggingface.co/settings/tokens",
    ),
    ProviderPreset(
        name="Z.ai (GLM)",
        base_url="https://api.z.ai/api/paas/v4",
        category="paid",
        models=["glm-4.6", "glm-4.5", "glm-4.5-air"],
        key_url="https://z.ai/manage-apikey/apikey-list",
        note="Zhipu AI's GLM family — OpenAI-compatible v4 API.",
    ),
    ProviderPreset(
        name="Qwen (Alibaba Cloud)",
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        category="paid",
        models=["qwen-plus", "qwen-max", "qwen3-coder-plus", "qwq-32b"],
        key_url="https://bailian.console.aliyun.com/?apiKey=1",
        note="Qwen models through DashScope's OpenAI-compatible endpoint.",
    ),
    ProviderPreset(
        name="Moonshot (Kimi)",
        base_url="https://api.moonshot.ai/v1",
        category="paid",
        models=["kimi-k2-0905-preview", "kimi-k2-turbo-preview",
                "moonshot-v1-128k"],
        key_url="https://platform.moonshot.ai/console/api-keys",
        note="Kimi long-context models.",
    ),
    ProviderPreset(
        name="MiniMax",
        base_url="https://api.minimax.io/v1",
        category="paid",
        models=["MiniMax-M2", "MiniMax-Text-01"],
        key_url="https://www.minimax.io/platform/interface-management",
        note="MiniMax M2 agentic coding model.",
    ),
    ProviderPreset(
        name="Azure OpenAI",
        base_url="https://{resource}.openai.azure.com/openai/v1",
        category="paid",
        models=["gpt-4.1", "gpt-4o", "o3-mini"],
        key_url="https://portal.azure.com/",
        url_fields=("resource",),
        note="Put your Azure resource name in the URL; key from the portal.",
    ),
    ProviderPreset(
        name="Hyperbolic",
        base_url="https://api.hyperbolic.xyz/v1",
        category="paid",
        models=["deepseek-ai/DeepSeek-V3.2", "Qwen/Qwen3-235B-A22B",
                "meta-llama/Llama-3.3-70B-Instruct"],
        key_url="https://app.hyperbolic.xyz/settings",
        note="GPU-cloud inference of open models.",
    ),
    ProviderPreset(
        name="DeepInfra",
        base_url="https://api.deepinfra.com/v1/openai",
        category="paid",
        models=["deepseek-ai/DeepSeek-V3.2-Exp",
                "meta-llama/Llama-3.3-70B-Instruct",
                "Qwen/Qwen3-235B-A22B-Thinking-2507"],
        key_url="https://deepinfra.com/dash/api_keys",
        note="Serverless open models, per-token pricing.",
    ),
    ProviderPreset(
        name="Nebius",
        base_url="https://api.nebius.com/v1",
        category="paid",
        models=["meta-llama/LLaMA-3.3-70B-Instruct",
                "moonshotai/Kimi-K2-Instruct",
                "deepseek-ai/DeepSeek-V3"],
        key_url="https://tokenfactory.nebius.com/my/api-keys",
        note="Token Factory AI inference endpoint.",
    ),
    ProviderPreset(
        name="Scaleway",
        base_url="https://app.scale.com/v1",
        category="paid",
        models=["llama-3.3-70b-instruct", "deepseek-r1-distill-llama-70b"],
        key_url="https://console.scale.com/iam/api-keys",
        note="Generative Agents API — EU-hosted.",
    ),
    ProviderPreset(
        name="StepFun",
        base_url="https://api.stepfun.com/v1",
        category="paid",
        models=["step-3.5-flash", "step-1-8k"],
        key_url="https://platform.stepfun.com/interface-key",
        note="StepFun's OpenAI-compatible API.",
    ),
    ProviderPreset(
        name="SiliconFlow",
        base_url="https://api.siliconflow.com/v1",
        category="paid",
        models=["deepseek-ai/DeepSeek-V3.2-Exp",
                "Qwen/Qwen3-235B-A22B-Thinking-2507",
                "moonshotai/Kimi-K2-Instruct-0905"],
        key_url="https://cloud.siliconflow.com/account/apikey",
        note="High-throughput open-model serving.",
    ),
    ProviderPreset(
        name="AIMLAPI",
        base_url="https://api.aimlapi.com/v1",
        category="paid",
        models=["openai/gpt-4.1", "google/gemini-2.5-flash",
                "meta-llama/llama-3.3-70b-instruct"],
        key_url="https://aimlapi.com/app/keys",
        note="One key for hundreds of models across families.",
    ),
    # ---- local (no key, no internet) ----------------------------------------
    ProviderPreset(
        name="Ollama (Local)",
        base_url="http://localhost:11434/v1",
        category="local",
        models=["llama3.2", "qwen2.5:7b", "gemma3:12b"],
        key_optional=True,
        note="Detected automatically if the server is running.",
    ),
    ProviderPreset(
        name="LM Studio (Local)",
        base_url="http://localhost:1234/v1",
        category="local",
        models=["user-model"],
        key_optional=True,
        note="Any GGUF model you loaded; the probe reads the real list.",
    ),
    ProviderPreset(
        name="vLLM (Local)",
        base_url="http://localhost:8000/v1",
        category="local",
        models=["served-model"],
        key_optional=True,
        note="For power users serving their own checkpoints.",
    ),
]

BY_NAME: dict[str, ProviderPreset] = {p.name: p for p in PROVIDER_CATALOG}

CATEGORIES = ("free", "paid", "local")


def get_preset(name: str) -> ProviderPreset | None:
    return BY_NAME.get(name)


def catalog_payload() -> list[dict[str, object]]:
    """Serializable form for the /api/providers/catalog endpoint."""
    return [
        {
            "name": p.name,
            "base_url": p.base_url,
            "category": p.category,
            "models": list(p.models),
            "key_url": p.key_url,
            "key_optional": p.key_optional,
            "note": p.note,
            "url_fields": list(p.url_fields),
        }
        for p in PROVIDER_CATALOG
    ]
