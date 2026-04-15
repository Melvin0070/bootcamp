# Activity 9: Remove Hard-Coded Credentials from the Codebase

**Week:** 2 | **Day:** 9 | **Course alignment:** System Design Foundations

## Problem Statement

API keys and AWS credentials are **hard-coded in source files** — a critical security vulnerability that could expose secrets if the repo is ever made public or cloned.

## What to Fix

- [ ] Audit the codebase for hard-coded secrets (API keys, AWS keys, tokens, passwords)
- [ ] Replace with **environment variables** (via `.env` file, never committed)
- [ ] For production: use **AWS Secrets Manager** or **Parameter Store**
- [ ] Add a `.gitignore` entry for `.env` files
- [ ] Add a `pre-commit` hook or CI check to block future credential commits

## Acceptance Criteria

- Zero hard-coded secrets in any tracked file
- `.env.example` documents required variables (without values)
- Secrets Manager integration works for at least one secret in prod config

## PR Checklist

- [ ] Fix applied in `broken/` → working code committed
- [ ] Clear PR description (what was broken, what you changed, why)
- [ ] 2–5 min video walkthrough (before/after)

## Notes

_Add your findings, decisions, and observations here as you work._
