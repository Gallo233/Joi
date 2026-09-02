# Security Policy

## Reporting a vulnerability

Report privately, not in a public issue.

Use GitHub's private vulnerability reporting on this repository: **Security → Report a vulnerability**. It opens a thread visible only to you and the maintainer.

If that form is not available to you, open a public issue that says only that you have a security report and asks for a private channel. **Do not put the details, a reproduction, logs, screenshots, or any key in a public issue.**

## What to include

- What an attacker can do, and what they need to start (a local user, a malicious character package, a hostile web page, a compromised model provider).
- The smallest reproduction you have.
- The Joi version from **设置 → 关于**, and your macOS version.

Redact before sending: API keys, session tokens, conversation text, file paths that identify you, and screenshots of your own screen.

## Scope

In scope: anything that crosses one of Joi's own boundaries — a capability acting without the confirmation it requires, an approval that can be bypassed or spoofed, a sandboxed adapter reaching outside its scope, secrets reaching logs or events, a character or Skill package escaping its isolation, coordinates or personal data leaking into a channel that promises not to carry them.

Out of scope: what a user's own model provider does with data the user deliberately sent it, and behaviour that requires the user to have already granted the exact permission being complained about.

## What to expect

This is a personal project without a paid disclosure programme. You will get an acknowledgement, an honest assessment of whether it is a bug or a documented boundary (see `docs/KNOWN_ISSUES.md`), and credit in the changelog if you want it.
