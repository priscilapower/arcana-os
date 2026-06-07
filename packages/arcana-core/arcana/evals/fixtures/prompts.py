"""
Standard eval prompts — used consistently across runs for comparability.
Never change a prompt once it has results. Add new ones instead.
"""

# Research domain — tests depth, citation, uncertainty handling
RESEARCH_PROMPTS = {
    "rag_vs_finetune": (
        "What are the key tradeoffs between RAG and fine-tuning for "
        "production LLMs? Be specific about cost, latency, and maintenance."
    ),
    "transformer_explained": (
        "Explain the transformer architecture to a software engineer "
        "who understands neural networks but has never studied NLP."
    ),
    "llm_hallucination": (
        "What are the root causes of hallucination in large language models "
        "and what mitigation strategies have the most empirical support?"
    ),
    "vector_db_tradeoffs": (
        "Compare Qdrant, Pinecone, and pgvector for a production RAG system "
        "serving 10M queries/day. What factors should drive the choice?"
    ),
}

# Creative domain — tests tone, richness, generativity
CREATIVE_PROMPTS = {
    "blog_post_ai_agents": (
        "Write an introduction for a blog post about why AI agents need "
        "persistent memory. Target audience: technical founders."
    ),
    "unconventional_approach": (
        "Propose an unconventional approach to reducing LLM hallucinations "
        "that hasn't been widely explored yet."
    ),
    "product_description": (
        "Write a product description for an AI agent OS where each agent "
        "is configured with a tarot card archetype."
    ),
}

# Behavioural — tests card-specific traits
BEHAVIOURAL_PROMPTS = {
    "ask_before_acting": (
        "I need help with my project. Can you help me?"
    ),
    "tool_vs_reason": (
        "What is the current price of AAPL stock?"
    ),
    "hard_truth": (
        "My startup idea is to build yet another to-do app with AI. "
        "What do you think?"
    ),
    "ambiguous_brief": (
        "Make it better."
    ),
}
