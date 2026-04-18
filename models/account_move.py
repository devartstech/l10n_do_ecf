# -*- coding: utf-8 -*-

import base64
import io
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .gae_service import GaeService

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    # ------------------------------------------------------------------
    # Campos GAE
    # ------------------------------------------------------------------

    gae_status = fields.Selection(
        selection=[
            ("pending", "Pendiente"),
            ("sent", "Enviado"),
            ("approved", "Aprobado"),
            ("rejected", "Rechazado"),
            ("contingency", "Contingencia"),
        ],
        string="Estado GAE",
        default="pending",
        readonly=True,
        copy=False,
        tracking=True,
        help="Estado del comprobante en el Gestor de Autorizaciones Electrónicas (GAE) "
             "de la DGII.",
    )
    gae_security_code = fields.Char(
        string="Código de Seguridad",
        readonly=True,
        copy=False,
        help="Código de seguridad devuelto por el GAE tras la firma exitosa del e-CF.",
    )
    gae_sign_url = fields.Char(
        string="URL Consulta DGII",
        readonly=True,
        copy=False,
        help="URL de consulta del comprobante en el portal de la DGII.",
    )
    gae_sign_date = fields.Datetime(
        string="Fecha Firma",
        readonly=True,
        copy=False,
        help="Fecha y hora en que el GAE firmó el comprobante.",
    )
    gae_error_msg = fields.Char(
        string="Error GAE",
        readonly=True,
        copy=False,
        help="Último mensaje de error recibido del GAE.",
    )
    l10n_do_ecf_modification_code = fields.Selection(
        selection=[
            ("1", "01 - Anulación de comprobante"),
            ("2", "02 - Corrección de monto"),
            ("3", "03 - Cambio de tipo de comprobante"),
            ("4", "04 - Corrección de datos del comprador"),
            ("5", "05 - Otros"),
        ],
        string="Razón de Modificación (e-CF)",
        copy=False,
        help="Código de razón de modificación requerido para notas de crédito (E34) "
             "y débito (E33) electrónicas.",
    )
    l10n_do_ecf_sequence_exp_date = fields.Date(
        string="Vencimiento Secuencia e-CF",
        copy=False,
        help="Fecha de vencimiento de la secuencia fiscal electrónica. "
             "Se usa cuando no está instalado l10n_do_accounting.",
    )
    is_ecf_applicable = fields.Boolean(
        string="Aplica e-CF",
        compute="_compute_is_ecf_applicable",
        store=True,
        help="Indica si este comprobante debe ser enviado al GAE como e-CF.",
    )

    # ------------------------------------------------------------------
    # Campos computados
    # ------------------------------------------------------------------

    @api.depends(
        "company_id",
        "company_id.l10n_do_ecf_issuer",
        "l10n_latam_document_type_id",
        "l10n_latam_document_type_id.doc_code_prefix",
        "move_type",
    )
    def _compute_is_ecf_applicable(self):
        """
        Un comprobante aplica e-CF cuando:
        1. La empresa está habilitada como emisora de e-CF
           (company.l10n_do_ecf_issuer = True).
        2. El tipo de documento es electrónico, es decir su prefijo empieza por 'E'
           seguido de dos dígitos (E31, E32, E33, E34, E41, E43, E44, E45, E46, E47).
        3. El comprobante es del tipo movimiento de factura/reembolso (no entradas
           de diario generales).
        """
        invoice_types = {
            "out_invoice", "out_refund", "in_invoice", "in_refund"
        }
        for move in self:
            is_issuer = move.company_id.l10n_do_ecf_issuer
            doc_type = move.l10n_latam_document_type_id
            prefix = (doc_type.doc_code_prefix or "").upper().strip() if doc_type else ""
            is_ecf_doc = (
                prefix.startswith("E")
                and len(prefix) == 3
                and prefix[1:].isdigit()
            )
            is_invoice_type = move.move_type in invoice_types
            move.is_ecf_applicable = bool(is_issuer and is_ecf_doc and is_invoice_type)

    # ------------------------------------------------------------------
    # Acciones de botón
    # ------------------------------------------------------------------

    def action_send_to_gae(self):
        """
        Envía el comprobante al GAE de la DGII.
        Actualiza los campos gae_* según la respuesta.
        Puede ser llamado desde el botón "Enviar a DGII (eCF)".
        """
        self.ensure_one()

        if not self.is_ecf_applicable:
            raise UserError(
                _("Este comprobante no aplica para envío electrónico (e-CF).")
            )
        if self.state != "posted":
            raise UserError(
                _("Solo se pueden enviar al GAE comprobantes confirmados (publicados).")
            )
        if not self.l10n_latam_document_number:
            raise UserError(
                _("El comprobante no tiene número de documento fiscal asignado.")
            )

        service = GaeService(self.company_id)

        try:
            result = service.send_invoice(self)
        except UserError as e:
            # Guardamos el error para que quede visible en la vista
            self.write({
                "gae_status": "rejected",
                "gae_error_msg": str(e.args[0])[:512] if e.args else "Error desconocido",
            })
            raise

        except Exception as e:
            _logger.exception(
                "GAE | Error inesperado al enviar e-CF %s",
                self.l10n_latam_document_number,
            )
            self.write({
                "gae_status": "rejected",
                "gae_error_msg": str(e)[:512],
            })
            raise UserError(
                _("Error inesperado al enviar el comprobante al GAE: %s") % str(e)
            )

        # Parsear fecha de firma
        sign_date = False
        raw_date = result.get("date", "")
        if raw_date:
            try:
                from datetime import datetime
                # El GAE puede devolver distintos formatos; intentamos los más comunes
                for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
                    try:
                        sign_date = datetime.strptime(raw_date[:26], fmt)
                        break
                    except ValueError:
                        continue
            except Exception:
                _logger.warning("GAE | No se pudo parsear la fecha de firma: %s", raw_date)

        self.write({
            "gae_status": "sent",
            "gae_error_msg": False,
        })

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("e-CF Enviado"),
                "message": _(
                    "El comprobante %s fue enviado al GAE. "
                    "GAE procesa en lote — use 'Verificar Estado GAE' para obtener el resultado."
                ) % self.l10n_latam_document_number,
                "type": "success",
                "sticky": False,
            },
        }

    def action_check_gae_status(self):
        """
        Consulta el estado actual del comprobante en el GAE.
        Actualiza gae_status según la respuesta.
        """
        self.ensure_one()

        if not self.is_ecf_applicable:
            raise UserError(
                _("Este comprobante no aplica para consulta de estado e-CF.")
            )
        if not self.l10n_latam_document_number:
            raise UserError(
                _("El comprobante no tiene número de documento fiscal asignado.")
            )

        service = GaeService(self.company_id)
        rnc = self.company_id.vat or ""
        ecf = self.l10n_latam_document_number

        # 1. Consultar estado de procesamiento batch (GetInvoiceStatus)
        try:
            status_data = service.get_invoice_status(rnc, ecf)
        except UserError as e:
            self.write({"gae_error_msg": str(e.args[0])[:512] if e.args else "Error"})
            raise
        except Exception as e:
            _logger.exception("GAE | Error inesperado al consultar estado de e-CF %s", ecf)
            raise UserError(
                _("Error inesperado al consultar estado en el GAE: %s") % str(e)
            )

        # Mapeo de estados posibles del GAE a valores internos
        gae_state_raw = (
            str(
                status_data.get("status") or status_data.get("Status")
                or status_data.get("estado") or ""
            )
            .lower()
            .strip()
        )
        status_map = {
            "aceptado": "approved",
            "aprobado": "approved",
            "approved": "approved",
            "rechazado": "rejected",
            "rejected": "rejected",
            "enviado": "sent",
            "sent": "sent",
            "contingencia": "contingency",
            "contingency": "contingency",
            "pendiente": "pending",
            "pending": "pending",
        }
        new_status = status_map.get(gae_state_raw, self.gae_status)
        write_vals = {"gae_status": new_status, "gae_error_msg": False}

        # Si fue rechazado, capturar detalle del error desde 'details'
        if new_status == "rejected":
            details = status_data.get("details") or []
            if details and isinstance(details, list):
                error_msgs = [str(d.get("description") or d.get("message") or d) for d in details]
                write_vals["gae_error_msg"] = "; ".join(error_msgs)[:512]

        # 2. Si fue aprobado, obtener timbre (GetInvoiceInfo → code, url, date)
        if new_status == "approved":
            try:
                info = service.get_invoice_info(rnc, ecf)
                if info.get("code"):
                    write_vals["gae_security_code"] = info["code"]
                if info.get("url"):
                    write_vals["gae_sign_url"] = info["url"]
                raw_date = info.get("date", "")
                if raw_date:
                    from datetime import datetime
                    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
                        try:
                            write_vals["gae_sign_date"] = datetime.strptime(raw_date[:26], fmt)
                            break
                        except ValueError:
                            continue
            except Exception:
                _logger.warning(
                    "GAE | No se pudo obtener timbre del e-CF %s aprobado", ecf
                )

        self.write(write_vals)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Estado GAE"),
                "message": _(
                    "Estado del e-CF %s en el GAE: %s"
                ) % (ecf, new_status.upper()),
                "type": "info" if new_status != "rejected" else "danger",
                "sticky": new_status == "rejected",
            },
        }

    # ------------------------------------------------------------------
    # Reporte impreso
    # ------------------------------------------------------------------

    def _get_gae_qr_code_src(self):
        """Genera el QR code de la URL DGII como data URI (PNG base64)."""
        self.ensure_one()
        if not self.gae_sign_url:
            return ""
        try:
            import qrcode
            qr = qrcode.QRCode(version=None, box_size=4, border=2)
            qr.add_data(self.gae_sign_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            return "data:image/png;base64," + b64
        except Exception:
            _logger.warning(
                "GAE | No se pudo generar QR para %s",
                self.l10n_latam_document_number,
            )
            return ""
