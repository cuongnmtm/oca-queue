# Copyright 2019 ACSONE SA/NV
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import base64
import json
import operator

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.web.controllers.main import CSVExport, ExcelExport


class DelayExport(models.Model):

    _name = "delay.export"
    _description = "Allow to delay the export"

    user_id = fields.Many2one("res.users", string="User", index=True)
    model_description = fields.Char()
    url = fields.Char()
    expiration_date = fields.Date()

    @api.model
    def delay_export(self, data):
        params = json.loads(data.get("data"))
        if not self.env.user.email:
            raise UserError(_("You must set an email address to your user."))
        self.with_delay().export(params)

    @api.model
    def _get_file_content(self, params):
        export_format = params.get("format")

        item_names = ("model", "fields", "ids", "domain", "import_compat", "context")
        items = operator.itemgetter(*item_names)(params)
        model_name, fields_name, ids, domain, import_compat, context = items
        user = self.env["res.users"].browse([context.get("uid")])
        if not user or not user.email:
            raise UserError(_("The user %s doesn't have an email address.") % user.name)

        model = self.env[model_name].with_context(
            import_compat=import_compat, **context
        )
        records = model.browse(ids) or model.search(
            domain, offset=0, limit=False, order=False
        )

        if not model._is_an_ordinary_table():
            fields_name = [field for field in fields_name if field["name"] != "id"]

        field_names = [f["name"] for f in fields_name]
        import_data = records.export_data(field_names).get("datas", [])

        if import_compat:
            columns_headers = field_names
        else:
            columns_headers = [val["label"].strip() for val in fields_name]

        if export_format == "csv":
            csv = CSVExport()
            return csv.from_data(columns_headers, import_data)
        else:
            xls = ExcelExport()
            return xls.from_data(columns_headers, import_data)

    @api.model
    def export(self, params):
        content = self._get_file_content(params)

        model_name, context, export_format = operator.itemgetter(
            "model", "context", "format"
        )(params)
        user = self.env["res.users"].browse([context.get("uid")])

        export_record = self.sudo().create(
            {
                "user_id": user.id,
            }
        )

        name = "{}.{}".format(model_name, export_format)
        attachment = self.env["ir.attachment"].create(
            {
                "name": name,
                "datas": base64.b64encode(content),
                "type": "binary",
                "res_model": self._name,
                "res_id": export_record.id,
            }
        )

        url = "{}/web/content/ir.attachment/{}/datas/{}?download=true".format(
            self.env["ir.config_parameter"].sudo().get_param("web.base.url"),
            attachment.id,
            attachment.name,
        )

        time_to_live = (
            self.env["ir.config_parameter"].sudo().get_param("attachment.ttl", 7)
        )
        date_today = fields.Date.today()
        expiration_date = fields.Date.to_string(
            date_today + relativedelta(days=int(time_to_live) + 1)
        )

        odoo_bot = self.sudo().env.ref("base.partner_root")
        email_from = odoo_bot.email
        model_description = self.env[model_name]._description

        export_record.write(
            {
                "url": url,
                "expiration_date": expiration_date,
                "model_description": model_description,
            }
        )

        self.env.ref("base_export_async.delay_export_mail_template").send_mail(
            export_record.id,
            email_values={
                "email_from": email_from,
                "reply_to": email_from,
            },
        )

    @api.model
    def cron_delete(self):
        date_today = fields.Date.today()
        self.search([("expiration_date", "<=", date_today)]).unlink()
