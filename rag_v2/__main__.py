from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from rag_v2.embeddings import FastEmbedder
from rag_v2.generation import (
    BedrockGroundedGenerator,
    GroqGroundedGenerator,
    OpenRouterGroundedGenerator,
)
from rag_v2.ingestion import build_index, load_jsonl_corpus
from rag_v2.models import RagRequest, RetrievalFilter
from rag_v2.retriever import HybridRetriever, RetrievalConfig
from rag_v2.service import RagService
from rag_v2.store import SQLiteVectorStore


def _embedder(args) -> FastEmbedder:
    return FastEmbedder(model_name=args.embedding_model, cache_dir=args.model_cache)


def _filter(args) -> RetrievalFilter:
    return RetrievalFilter(
        course_id=args.course,
        topics=tuple(args.topic or ()),
        document_ids=tuple(args.document or ()),
    )


def _result_dict(result) -> dict:
    value = asdict(result)
    return value


def _build(args) -> int:
    documents = load_jsonl_corpus(args.corpus)
    embedder = _embedder(args)
    store = SQLiteVectorStore(args.index)
    count = build_index(documents, embedder=embedder, store=store)
    print(
        json.dumps(
            {
                "status": "built",
                "documents": len(documents),
                "chunks": count,
                "index": str(Path(args.index).resolve()),
                "embedding_model": embedder.model_id,
            },
            indent=2,
        )
    )
    return 0


def _retriever(args) -> HybridRetriever:
    return HybridRetriever(
        SQLiteVectorStore(args.index),
        _embedder(args),
        RetrievalConfig(min_score=args.min_score),
    )


def _retrieve(args) -> int:
    results = _retriever(args).retrieve(args.question, filters=_filter(args), top_k=args.top_k)
    print(json.dumps([_result_dict(result) for result in results], indent=2, ensure_ascii=False))
    return 0 if results else 2


def _query(args) -> int:
    provider = args.generation_provider
    if provider == "bedrock":
        model = args.generation_model or "openai.gpt-oss-120b-1:0"
        generator = BedrockGroundedGenerator(model=model)
        api_key = "aws-credential-chain"
        key_name = "AWS credential chain"
    elif provider == "openrouter":
        api_key = os.getenv("OPEN_ROUTER_API_KEY", "") or os.getenv("OPENROUTER_API_KEY", "")
        model = args.generation_model or "openai/gpt-oss-120b"
        generator = (
            OpenRouterGroundedGenerator(
                api_key=api_key,
                model=model,
                base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                site_url=os.getenv("OPENROUTER_SITE_URL", ""),
                app_name=os.getenv("OPENROUTER_APP_NAME", "Timmins WhatsApp Assistant"),
            )
            if api_key
            else None
        )
        key_name = "OPEN_ROUTER_API_KEY (or OPENROUTER_API_KEY)"
    else:
        api_key = os.getenv("GROQ_API_KEY", "")
        model = args.generation_model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        generator = GroqGroundedGenerator(api_key=api_key, model=model) if api_key else None
        key_name = "GROQ_API_KEY"
    if not api_key:
        raise SystemExit(f"{key_name} is required for {provider} generation")
    service = RagService(
        _retriever(args),
        generator,
    )
    answer = service.answer(
        RagRequest(
            question=args.question,
            retrieval_filter=_filter(args),
            require_course_scope=args.require_course,
            top_k=args.top_k,
        )
    )
    print(json.dumps(asdict(answer), indent=2, ensure_ascii=False))
    return 0 if answer.status == "answered" else 2


def _add_shared_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--index", default="var/rag_v2.sqlite")
    parser.add_argument("--embedding-model", default="BAAI/bge-small-en-v1.5")
    parser.add_argument("--model-cache")


def _add_query_arguments(parser: argparse.ArgumentParser) -> None:
    _add_shared_arguments(parser)
    parser.add_argument("question")
    parser.add_argument("--course")
    parser.add_argument("--topic", action="append")
    parser.add_argument("--document", action="append")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-score", type=float, default=0.42)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and query the independent RAG v2 index")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="validate a JSONL corpus and rebuild the index")
    _add_shared_arguments(build)
    build.add_argument("--corpus", required=True)
    build.set_defaults(handler=_build)

    retrieve = subparsers.add_parser("retrieve", help="inspect retrieval without generation")
    _add_query_arguments(retrieve)
    retrieve.set_defaults(handler=_retrieve)

    query = subparsers.add_parser("query", help="retrieve and generate a grounded answer")
    _add_query_arguments(query)
    query.add_argument(
        "--generation-provider",
        choices=("bedrock", "groq", "openrouter"),
        default=os.getenv("RAG_GENERATION_PROVIDER", "groq"),
    )
    query.add_argument("--generation-model", default=os.getenv("RAG_GENERATION_MODEL") or None)
    query.add_argument("--require-course", action="store_true")
    query.set_defaults(handler=_query)
    return parser


def main() -> int:
    load_dotenv()
    args = _parser().parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
