"""Exceções da camada de orquestração."""

from __future__ import annotations


class ServiceError(Exception):
    """Erro genérico da camada de serviço."""


class NothingPlayingError(ServiceError):
    """Comando de controle de reprodução (pause/resume/skip/stop) sem reprodução ativa."""


class InvalidVolumeError(ServiceError):
    """Volume fora do intervalo permitido (0-200)."""


class InvalidSearchIndexError(ServiceError):
    """`/pick <índice>` fora dos limites da última busca de addons (ou nenhuma busca feita)."""


class NoStreamsAvailableError(ServiceError):
    """O addon não devolveu nenhuma fonte reproduzível para o resultado escolhido."""


class TorrentTimeoutError(ServiceError):
    """Timeout aguardando metadata ou buffer mínimo de um torrent (`TorrentService.resolve`).

    Recuperável: `AddonService.pick()` trata como sinal para tentar o
    próximo candidato da lista, não como falha definitiva.
    """


class TorrentResolutionError(ServiceError):
    """Falha não recuperável ao resolver um torrent.

    Cobre: autenticação, indisponibilidade da API do qBittorrent, magnet
    inválido, nenhum arquivo de vídeo encontrado, ou espaço em disco
    insuficiente.
    """
