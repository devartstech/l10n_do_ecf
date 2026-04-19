# -*- coding: utf-8 -*-

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    """
    Extiende la vista de Ajustes para exponer los campos GAE de res.company
    como campos relacionados editables desde el panel de configuración.
    """
    _inherit = "res.config.settings"

    gae_api_key = fields.Char(
        string="GAE API Key",
        related="company_id.gae_api_key",
        readonly=False,
        help="Clave de autenticación para el GAE de la DGII (se envía como header 'ApiKey').",
    )
    gae_api_url = fields.Char(
        string="GAE API URL",
        related="company_id.gae_api_url",
        readonly=False,
        help="URL base del servicio GAE. Ejemplo: https://ecf.dgii.gov.do/api",
    )
    gae_environment = fields.Selection(
        string="Entorno GAE",
        related="company_id.gae_environment",
        readonly=False,
        help="Entorno de conexión: Test (certificación DGII) o Producción.",
    )
    gae_seller_code = fields.Char(
        string="Código Punto de Venta",
        related="company_id.gae_seller_code",
        readonly=False,
        help="Código del punto de venta requerido por el GAE (por defecto '001').",
    )
    l10n_do_ecf_issuer = fields.Boolean(
        string="Es emisor de e-CF",
        related="company_id.l10n_do_ecf_issuer",
        readonly=False,
        help="Habilita la emisión de Comprobantes Fiscales Electrónicos (e-CF).",
    )
