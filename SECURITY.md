# Segurança · Security · Seguridad

## Português

Não publique vulnerabilidades em issues. Use o recurso **Security Advisories** do GitHub para reportar exposição de `SESSION_STRING`, bypass de autorização, execução indevida de addons, injeção em FFmpeg ou acesso fora do diretório de mídia. Inclua versão, impacto e reprodução mínima, sem segredos reais.

Addons executam no mesmo processo que as credenciais Telegram e devem ser tratados como código totalmente confiável. Se um segredo vazar: revogue o token no BotFather, encerre as sessões Telegram, troque a senha do qBittorrent e remova o segredo de todo o histórico Git.

## English

Do not disclose vulnerabilities in public issues. Use GitHub **Security Advisories** for session exposure, authorization bypasses, unsafe addon execution, FFmpeg injection or media-directory escapes. Include the affected version, impact and a minimal reproduction without real credentials.

Addons share the process that holds Telegram credentials and must be treated as fully trusted code. After a leak, revoke the BotFather token, terminate Telegram sessions, rotate qBittorrent credentials and purge the secret from Git history.

## Español

No publiques vulnerabilidades en issues públicas. Usa **Security Advisories** de GitHub para exposición de sesiones, bypass de autorización, ejecución insegura de addons, inyección en FFmpeg o escapes del directorio multimedia. Incluye versión, impacto y reproducción mínima sin credenciales reales.

Los addons comparten el proceso que contiene las credenciales de Telegram y deben considerarse código totalmente confiable. Si ocurre una filtración, revoca el token de BotFather, cierra las sesiones de Telegram, cambia las credenciales de qBittorrent y elimina el secreto del historial Git.
