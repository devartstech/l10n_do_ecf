# -*- coding: utf-8 -*-

import logging
from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

# IDs externos de los tipos de documento creados por este módulo
_OUR_DOC_TYPE_XMLIDS = [
    "l10n_do_ecf.ecf_e31_fiscal",
    "l10n_do_ecf.ecf_e32_consumer",
    "l10n_do_ecf.ecf_e33_debit_note",
    "l10n_do_ecf.ecf_e34_credit_note",
    "l10n_do_ecf.ecf_e41_purchase",
    "l10n_do_ecf.ecf_e43_minor",
    "l10n_do_ecf.ecf_e44_special",
    "l10n_do_ecf.ecf_e45_gov",
    "l10n_do_ecf.ecf_e46_export",
    "l10n_do_ecf.ecf_e47_exterior",
]


def _archive_our_doc_types_if_needed(env):
    """
    Si l10n_do_accounting está instalado, archiva los tipos de documento ECF
    creados por este módulo para evitar duplicados en el selector de facturas.
    l10n_do_accounting ya provee sus propios registros E31-E47 con secuencias.
    Corre tanto en instalación nueva como en cada actualización del módulo.
    """
    l10n_do_accounting = env["ir.module.module"].search(
        [("name", "=", "l10n_do_accounting"), ("state", "=", "installed")],
        limit=1,
    )
    if not l10n_do_accounting:
        return

    _logger.info(
        "l10n_do_ecf: l10n_do_accounting detectado — archivando tipos de documento "
        "propios para evitar duplicados."
    )

    records_to_archive = env["l10n_latam.document.type"]
    for xmlid in _OUR_DOC_TYPE_XMLIDS:
        rec = env.ref(xmlid, raise_if_not_found=False)
        if rec and rec.active:
            records_to_archive |= rec

    if records_to_archive:
        records_to_archive.write({"active": False})
        _logger.info(
            "l10n_do_ecf: %d tipos de documento archivados: %s",
            len(records_to_archive),
            ", ".join(records_to_archive.mapped("doc_code_prefix")),
        )


def post_init_hook(env):
    _archive_our_doc_types_if_needed(env)


def post_migrate(env, version):
    _archive_our_doc_types_if_needed(env)
