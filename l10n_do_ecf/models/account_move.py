# -*- coding: utf-8 -*-

import base64
import io
import logging
from datetime import datetime

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .gae_service import GaeService, GaeNetworkError

_logger = logging.getLogger(__name__)

# Etiquetas legibles para notificaciones al usuario
_GAE_STATUS_LABELS = {
    "pending": "Pendiente",
    "sent": "Enviado (procesando)",
    "approved": "Aprobado",
    "rejected": "Rechazado",
    "contingency": "Contingencia",
}


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
    gae_error_msg = fields.Text(
        string="Error GAE",
        readonly=True,
        copy=False,
        help="Último mensaje de error recibido del GAE.",
    )
    gae_last_attempt_date = fields.Datetime(
        string="Último Intento GAE",
        readonly=True,
        copy=False,
        help="Fecha y hora del último intento de envío o consulta al GAE.",
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
        invoice_types = {"out_invoice", "out_refund", "in_invoice", "in_refund"}
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
    # Utilidades internas
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_gae_date(raw_date):
        """Parsea la fecha de firma devuelta por el GAE en distintos formatos."""
        if not raw_date:
            return False
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                return datetime.strptime(raw_date[:26], fmt)
            except ValueError:
                continue
        _logger.warning("GAE | No se pudo parsear fecha de firma: %s", raw_date)
        return False

    # ------------------------------------------------------------------
    # Acciones de botón
    # ------------------------------------------------------------------

    def action_send_to_gae(self):
        """
        Envía el comprobante al GAE de la DGII.
        Actualiza los campos gae_* según la respuesta.
        """
        self.ensure_one()

        if not self.is_ecf_applicable:
            raise UserError(_("Este comprobante no aplica para envío electrónico (e-CF)."))
        if self.state != "posted":
            raise UserError(_("Solo se pueden enviar al GAE comprobantes confirmados (publicados)."))
        if not self.l10n_latam_document_number:
            raise UserError(_("El comprobante no tiene número de documento fiscal asignado."))

        service = GaeService(self.company_id)

        # Validación pre-vuelo: detecta problemas antes de llamar al GAE
        service.validate_pre_flight(self)

        # Registrar intento
        self.write({"gae_last_attempt_date": fields.Datetime.now()})

        try:
            result = service.send_invoice(self)

        except GaeNetworkError as e:
            # Error de red: no cambiar el estado GAE (el comprobante puede o no haber llegado)
            err_msg = str(e.args[0]) if e.args else "Error de conexión con el GAE"
            _logger.warning(
                "GAE | Error de red al enviar e-CF %s: %s",
                self.l10n_latam_document_number, err_msg,
            )
            self.write({"gae_error_msg": err_msg})
            raise UserError(
                _("No se pudo conectar con el GAE para enviar el e-CF %s.\n\n%s\n\n"
                  "El comprobante puede haber llegado al GAE igualmente. "
                  "Use 'Verificar Estado GAE' para confirmarlo.")
                % (self.l10n_latam_document_number, err_msg)
            )

        except UserError as e:
            # Rechazo o error de validación: marcar como rechazado
            err_msg = str(e.args[0]) if e.args else "Error desconocido"
            self.write({
                "gae_status": "rejected",
                "gae_error_msg": err_msg,
            })
            raise

        except Exception as e:
            _logger.exception(
                "GAE | Error inesperado al enviar e-CF %s", self.l10n_latam_document_number
            )
            err_msg = str(e)
            self.write({
                "gae_status": "rejected",
                "gae_error_msg": err_msg,
            })
            raise UserError(
                _("Error inesperado al enviar el comprobante al GAE: %s") % err_msg
            )

        sign_date = self._parse_gae_date(result.get("date", ""))

        self.write({
            "gae_status": "sent",
            "gae_sign_date": sign_date or False,
            "gae_error_msg": False,
        })

        _logger.info(
            "GAE | e-CF %s enviado correctamente — estado: sent", self.l10n_latam_document_number
        )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("e-CF Enviado al GAE"),
                "message": _(
                    "El comprobante %s fue enviado al GAE correctamente. "
                    "GAE procesa en lote — use 'Verificar Estado GAE' para obtener el resultado final."
                ) % self.l10n_latam_document_number,
                "type": "success",
                "sticky": False,
                "next": {
                    "type": "ir.actions.act_window",
                    "res_model": "account.move",
                    "res_id": self.id,
                    "view_mode": "form",
                    "views": [(False, "form")],
                    "target": "current",
                },
            },
        }

    def action_check_gae_status(self):
        """
        Consulta el estado actual del comprobante en el GAE.
        Actualiza gae_status según la respuesta.
        """
        self.ensure_one()

        if not self.is_ecf_applicable:
            raise UserError(_("Este comprobante no aplica para consulta de estado e-CF."))
        if not self.l10n_latam_document_number:
            raise UserError(_("El comprobante no tiene número de documento fiscal asignado."))

        service = GaeService(self.company_id)
        rnc = self.company_id.vat or ""
        ecf = self.l10n_latam_document_number

        # Registrar intento
        self.write({"gae_last_attempt_date": fields.Datetime.now()})

        # 1. Consultar estado de procesamiento batch (GetInvoiceStatus)
        try:
            status_data = service.get_invoice_status(rnc, ecf)
        except GaeNetworkError as e:
            err_msg = str(e.args[0]) if e.args else "Error de conexión"
            self.write({"gae_error_msg": err_msg})
            raise UserError(
                _("No se pudo conectar con el GAE para consultar el estado del e-CF %s.\n\n%s")
                % (ecf, err_msg)
            )
        except UserError as e:
            err_msg = str(e.args[0]) if e.args else "Error"
            self.write({"gae_error_msg": err_msg})
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
        new_status = status_map.get(gae_state_raw)
        if new_status is None:
            # Estado desconocido: conservar el actual y loguear para investigación
            _logger.warning(
                "GAE | Estado desconocido '%s' para e-CF %s — se conserva estado actual '%s'",
                gae_state_raw, ecf, self.gae_status,
            )
            new_status = self.gae_status

        write_vals = {"gae_status": new_status, "gae_error_msg": False}

        # Si fue rechazado, capturar detalle del error desde 'details'
        if new_status == "rejected":
            details = status_data.get("details") or []
            if details and isinstance(details, list):
                error_msgs = []
                for d in details:
                    if isinstance(d, dict):
                        code = d.get("code") or d.get("Code") or ""
                        desc = d.get("description") or d.get("message") or d.get("Description") or str(d)
                        error_msgs.append("[{}] {}".format(code, desc) if code else str(desc))
                    else:
                        error_msgs.append(str(d))
                write_vals["gae_error_msg"] = "\n".join(error_msgs)

        # 2. Si fue aprobado, obtener timbre (GetInvoiceInfo → code, url, date)
        if new_status == "approved":
            try:
                info = service.get_invoice_info(rnc, ecf)
                if info.get("code"):
                    write_vals["gae_security_code"] = info["code"]
                if info.get("url"):
                    write_vals["gae_sign_url"] = info["url"]
                sign_date = self._parse_gae_date(info.get("date", ""))
                if sign_date:
                    write_vals["gae_sign_date"] = sign_date
            except Exception:
                _logger.warning(
                    "GAE | No se pudo obtener timbre del e-CF %s aprobado — "
                    "use 'Verificar Estado GAE' nuevamente para reintentarlo.",
                    ecf,
                )

        self.write(write_vals)

        _logger.info(
            "GAE | Estado consultado para e-CF %s: %s → %s",
            ecf, gae_state_raw, new_status,
        )

        status_label = _GAE_STATUS_LABELS.get(new_status, new_status.upper())
        notif_type = "success" if new_status == "approved" else (
            "danger" if new_status == "rejected" else "info"
        )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Estado GAE: %s") % status_label,
                "message": _(
                    "El e-CF %s tiene estado '%s' en el GAE."
                ) % (ecf, status_label),
                "type": notif_type,
                "sticky": new_status == "rejected",
                "next": {
                    "type": "ir.actions.act_window",
                    "res_model": "account.move",
                    "res_id": self.id,
                    "view_mode": "form",
                    "views": [(False, "form")],
                    "target": "current",
                },
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
                "GAE | No se pudo generar QR para %s", self.l10n_latam_document_number
            )
            return ""
