# resource-engine-jinja

Jinja2 templates for Resource Engine: `JinjaTemplates`, a `TemplateRenderer`
for emails and generated documents.

- Templates from a folder or shipped inside a package (`package=`, `folder=`).
- Locale fallback (`ca-ES/…`, `ca/…`, the default locale, unlocalised).
- A sandboxed environment, autoescaping for `.html` and `.xml`, and undefined
  variables as errors (RE-E011), never empty strings.

See [email](../../docs/email.md) and the
[adapter catalogue](../../docs/adapters.md). Part of [Resource Engine](../../README.md).
