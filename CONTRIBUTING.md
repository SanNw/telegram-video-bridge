# Contribuindo · Contributing · Contribuir

## Português

1. Abra uma issue para mudanças de comportamento; correções pequenas podem ir direto para PR.
2. Crie uma branch curta, preserve as camadas existentes e evite novas dependências sem necessidade comprovada.
3. Execute `uv sync --all-groups`, `uv run ruff check .`, `uv run black --check .`, `uv run mypy` e `uv run pytest`.
4. Alterações Docker também devem passar por `docker build -f docker/Dockerfile -t telerion:local .`.
5. Nunca inclua `.env`, sessões, tokens, senhas, mídia ou fontes sem autorização.

### Padrão editorial

Toda mudança pública deve atualizar português, inglês e espanhol no mesmo PR. Preserve o vocabulário cinematográfico, os ícones semânticos, o cabeçalho `docs/assets/telerion-header.svg`, a navegação de idiomas, tabelas curtas e seções expansíveis. Não adicione banners genéricos, excesso de badges ou emojis decorativos sem função.

## English

1. Open an issue for behavioral changes; small fixes may go directly to a PR.
2. Use a focused branch, preserve the existing layers and avoid new dependencies without a demonstrated need.
3. Run the complete quality command set listed above. Docker changes must also build locally.
4. Never include secrets, sessions, credentials, media or unauthorized sources.

### Editorial standard

Every public-facing change must update Portuguese, English and Spanish in the same PR. Preserve the cinematic vocabulary, semantic icons, project header, language navigation, compact tables and expandable sections. Avoid generic banners, badge walls and decorative emoji noise.

## Español

1. Abre una issue para cambios de comportamiento; las correcciones pequeñas pueden ir directamente a un PR.
2. Usa una rama enfocada, conserva las capas existentes y evita dependencias nuevas sin una necesidad demostrada.
3. Ejecuta todas las verificaciones de calidad indicadas arriba. Los cambios Docker también deben compilar localmente.
4. Nunca incluyas secretos, sesiones, credenciales, medios ni fuentes no autorizadas.

### Estándar editorial

Cada cambio público debe actualizar portugués, inglés y español en el mismo PR. Conserva el vocabulario cinematográfico, los iconos semánticos, la cabecera, la navegación por idiomas, las tablas compactas y las secciones desplegables. Evita banners genéricos, muros de insignias y emojis puramente decorativos.
