import logging
import asyncio
from typing import Optional, List, Dict, Any
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
import smtplib

try:
    import aiosmtplib
    HAS_AIOSMTPLIB = True
except ImportError:
    HAS_AIOSMTPLIB = False

from app.core.config import settings

logger = logging.getLogger("senwisetool.email")

class EmailService:
    """
    Service professionnel d'envoi d'e-mails transactionnels (SMTP asynchrone).
    Gère les templates HTML/Texte, la sécurité TLS/SSL, et le mode fallback simulation console.
    """

    @property
    def is_smtp_configured(self) -> bool:
        """Vérifie si les informations d'authentification SMTP sont renseignées."""
        return bool(
            settings.SMTP_ENABLED and
            settings.SMTP_HOST and
            settings.SMTP_USER and
            settings.SMTP_USER.strip() and
            settings.SMTP_PASSWORD and
            settings.SMTP_PASSWORD.strip()
        )

    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None
    ) -> bool:
        """
        Expédie un e-mail au destinataire via SMTP avec support HTML et Plain Text.
        Si SMTP n'est pas configuré, bascule en mode simulation terminal avec log explicite.
        """
        # Préparation du contenu texte brut si non fourni
        if not text_content:
            text_content = html_content.replace("<br>", "\n").replace("</p>", "\n\n")

        # Vérification si le service SMTP est opérationnel
        if not self.is_smtp_configured:
            print("\n" + "=" * 78)
            print("[SENWISETOOL EMAIL SERVICE - SIMULATION CONSOLE]")
            print(f"Destinataire : {to_email}")
            print(f"Sujet        : {subject}")
            print(f"Expéditeur   : {settings.EMAILS_FROM_NAME} <{settings.EMAILS_FROM_EMAIL}>")
            print(f"Raison       : SMTP_USER / SMTP_PASSWORD non renseignés dans .env")
            print("-" * 78)
            print(text_content.strip())
            print("=" * 78 + "\n")
            logger.info(f"[EMAIL SIMULÉ] E-mail simulé pour {to_email} (Sujet: '{subject}')")
            return True

        # Construction du message MIME Multipart
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{settings.EMAILS_FROM_NAME} <{settings.EMAILS_FROM_EMAIL}>"
        msg["To"] = to_email
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=settings.EMAILS_FROM_EMAIL.split("@")[-1])

        part_text = MIMEText(text_content, "plain", "utf-8")
        part_html = MIMEText(html_content, "html", "utf-8")
        msg.attach(part_text)
        msg.attach(part_html)

        def _send_sync() -> bool:
            clean_pwd = settings.SMTP_PASSWORD.strip().strip('"').strip("'")
            # Essai 1 : Connexion standard selon configuration .env
            try:
                if settings.SMTP_SSL:
                    server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
                else:
                    server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
                    server.ehlo()
                    if settings.SMTP_TLS:
                        server.starttls()
                        server.ehlo()
                server.login(settings.SMTP_USER, clean_pwd)
                server.send_message(msg)
                server.quit()
                return True
            except Exception as first_err:
                # Essai 2 : Fallback automatique SSL 465 si le transport TLS a été interrompu
                try:
                    fallback_port = 465 if settings.SMTP_PORT != 465 else 587
                    if fallback_port == 465:
                        fallback_server = smtplib.SMTP_SSL(settings.SMTP_HOST, 465, timeout=15)
                    else:
                        fallback_server = smtplib.SMTP(settings.SMTP_HOST, 587, timeout=15)
                        fallback_server.ehlo()
                        fallback_server.starttls()
                        fallback_server.ehlo()
                    fallback_server.login(settings.SMTP_USER, clean_pwd)
                    fallback_server.send_message(msg)
                    fallback_server.quit()
                    return True
                except Exception:
                    raise first_err

        try:
            await asyncio.to_thread(_send_sync)
            print(f"[SENWISETOOL EMAIL SERVICE] ✓ E-mail envoyé avec succès à {to_email} (Sujet: '{subject}')")
            logger.info(f"E-mail expédié avec succès à {to_email}")
            return True

        except Exception as e:
            # Erreur d'expédition SMTP (connexion refusée, identifiants invalides, etc.)
            print("\n" + "!" * 78)
            print(f"[SENWISETOOL EMAIL SERVICE - ERREUR EXPÉDITION SMTP]")
            print(f"Destinataire : {to_email}")
            print(f"Hôte SMTP    : {settings.SMTP_HOST}:{settings.SMTP_PORT}")
            print(f"Utilisateur  : {settings.SMTP_USER}")
            print(f"Erreur       : {str(e)}")
            print("-" * 78)
            print("CONTENU DU MESSAGE SAUVEGARDÉ POUR CONSULTATION :")
            print(text_content.strip())
            print("!" * 78 + "\n")
            logger.error(f"Échec envoi e-mail SMTP à {to_email}: {e}")
            return False

    async def send_organization_invitation_email(
        self,
        to_email: str,
        invite_url: str,
        expiration_hours: int = 48,
        custom_questions_count: int = 0,
    ) -> bool:
        """
        Envoie l'e-mail officiel d'invitation avec le lien unique d'onboarding.
        """
        subject = f"[SenWiseTool] Invitation à créer votre Organisation ({expiration_hours}h)"

        questions_badge_html = ""
        questions_badge_text = ""
        if custom_questions_count > 0:
            questions_badge_html = f"""
            <div style="background-color: #ecfdf5; border-left: 4px solid #10b981; padding: 12px 16px; margin: 18px 0; border-radius: 6px;">
                <p style="margin: 0; color: #065f46; font-size: 13px; font-weight: 600;">
                    📋 Questionnaire sur mesure inclus ({custom_questions_count} questions)
                </p>
                <p style="margin: 4px 0 0 0; color: #047857; font-size: 12px;">
                    Le Super-Administrateur a défini des vérifications spécifiques à remplir lors de votre inscription.
                </p>
            </div>
            """
            questions_badge_text = f"\nNote : Un questionnaire d'onboarding spécifique ({custom_questions_count} questions) a été joint à votre demande.\n"

        html_content = f"""
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Invitation SenWiseTool</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b; line-height: 1.6;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f8fafc; padding: 40px 15px;">
        <tr>
            <td align="center">
                <table width="600" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.05), 0 8px 10px -6px rgba(0, 0, 0, 0.01); border: 1px solid #e2e8f0;">
                    <!-- En-tête avec marque -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #07192c 0%, #0c2d4f 100%); padding: 32px 40px; text-align: left;">
                            <table width="100%" cellpadding="0" cellspacing="0">
                                <tr>
                                    <td>
                                        <div style="font-size: 22px; font-weight: 800; color: #ffffff; letter-spacing: -0.5px;">
                                            SenWise<span style="color: #10b981;">Tool</span>
                                        </div>
                                        <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; color: #94a3b8; margin-top: 4px; font-weight: 600;">
                                            Plateforme de Télémétrie & Traçabilité Écologique
                                        </div>
                                    </td>
                                    <td align="right">
                                        <span style="display: inline-block; background-color: rgba(16, 185, 129, 0.2); border: 1px solid rgba(16, 185, 129, 0.4); color: #34d399; font-size: 11px; font-weight: 700; padding: 4px 12px; border-radius: 20px; text-transform: uppercase;">
                                            Invitation Officielle
                                        </span>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <!-- Contenu principal -->
                    <tr>
                        <td style="padding: 40px;">
                            <h1 style="font-size: 20px; font-weight: 700; color: #0f172a; margin: 0 0 16px 0;">
                                Invitation à créer votre Organisation
                            </h1>
                            <p style="font-size: 14px; color: #475569; margin: 0 0 16px 0;">
                                Bonjour,
                            </p>
                            <p style="font-size: 14px; color: #475569; margin: 0 0 20px 0;">
                                L'administration de la plateforme <strong>SenWiseTool</strong> vous invite à présenter une demande de création de votre organisation.
                            </p>

                            <!-- Boîte d'expiration -->
                            <div style="background-color: #f1f5f9; border: 1px solid #cbd5e1; border-radius: 8px; padding: 14px 18px; margin: 20px 0;">
                                <table width="100%" cellpadding="0" cellspacing="0">
                                    <tr>
                                        <td width="24" style="vertical-align: top; padding-top: 2px;">
                                            <span style="font-size: 16px;">⏳</span>
                                        </td>
                                        <td style="padding-left: 10px;">
                                            <p style="margin: 0; font-size: 13px; font-weight: 700; color: #1e293b;">
                                                Lien à usage unique sécurisé
                                            </p>
                                            <p style="margin: 3px 0 0 0; font-size: 12px; color: #64748b;">
                                                Ce lien expirera dans <strong>{expiration_hours} heures</strong>. Passé ce délai, une nouvelle invitation est obligatoire pour présenter une demande.
                                            </p>
                                        </td>
                                    </tr>
                                </table>
                            </div>

                            {questions_badge_html}

                            <!-- Bouton CTA -->
                            <div style="text-align: center; margin: 32px 0;">
                                <a href="{invite_url}" target="_blank" style="display: inline-block; background-color: #00a86b; background: linear-gradient(135deg, #00a86b 0%, #008f5a 100%); color: #ffffff; font-size: 14px; font-weight: 700; text-decoration: none; padding: 14px 32px; border-radius: 10px; box-shadow: 0 4px 12px rgba(0, 168, 107, 0.35);">
                                    Activez & Presentez une demande &rarr;
                                </a>
                            </div>

                            <p style="font-size: 12px; color: #64748b; margin: 24px 0 8px 0;">
                                Si le bouton ci-dessus ne fonctionne pas, copiez et collez l'URL suivante dans votre navigateur :
                            </p>
                            <div style="background-color: #f8fafc; border: 1px dashed #cbd5e1; padding: 10px 14px; border-radius: 8px; font-family: monospace; font-size: 11px; color: #334155; word-break: break-all;">
                                {invite_url}
                            </div>

                            <div style="margin-top: 32px; padding-top: 20px; border-top: 1px solid #e2e8f0;">
                                <p style="font-size: 12px; color: #94a3b8; margin: 0;">
                                    <strong>Règle de sécurité :</strong> Si vous n'êtes pas la personne concernée, veuillez ignorer cet e-mail.
                                </p>
                            </div>
                        </td>
                    </tr>

                    <!-- Pied de page -->
                    <tr>
                        <td style="background-color: #f1f5f9; padding: 24px 40px; text-align: center; border-top: 1px solid #e2e8f0;">
                            <p style="font-size: 11px; color: #64748b; margin: 0 0 6px 0;">
                                Cet e-mail a été envoyé automatiquement par SenWiseTool Platform.
                            </p>
                            <p style="font-size: 11px; color: #94a3b8; margin: 0;">
                                &copy; 2026 SenWiseTool. Tous droits réservés.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
        """

        text_content = f"""
SenWiseTool - Invitation Officielle

Bonjour,

Le Super-Administrateur de SenWiseTool vous invite à inscrire votre organisation.
Ce lien d'inscription à usage unique expirera dans {expiration_hours} heures.
{questions_badge_text}
Pour activer votre espace, rendez-vous sur :
{invite_url}

Consignes de sécurité :
- Chaque compte est strictement lié à une seule organisation.
- Ne partagez pas ce lien.

© 2026 SenWiseTool Platform
        """

        return await self.send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content
        )

    async def send_otp_verification_email(
        self,
        to_email: str,
        otp_code: str,
        expiration_minutes: int = 15,
    ) -> bool:
        """
        Envoie l'e-mail contenant le code OTP à 6 chiffres pour valider l'identité.
        """
        subject = f"[SenWiseTool] Votre code de sécurité OTP : {otp_code}"

        html_content = f"""
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Code de sécurité OTP</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b; line-height: 1.6;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f8fafc; padding: 40px 15px;">
        <tr>
            <td align="center">
                <table width="560" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.05); border: 1px solid #e2e8f0;">
                    <!-- En-tête -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #07192c 0%, #0c2d4f 100%); padding: 28px 36px;">
                            <div style="font-size: 20px; font-weight: 800; color: #ffffff;">
                                SenWise<span style="color: #10b981;">Tool</span>
                            </div>
                            <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1.2px; color: #94a3b8; margin-top: 3px; font-weight: 600;">
                                Vérification de Sécurité
                            </div>
                        </td>
                    </tr>

                    <!-- Contenu -->
                    <tr>
                        <td style="padding: 36px;">
                            <h2 style="font-size: 18px; font-weight: 700; color: #0f172a; margin: 0 0 12px 0;">
                                Confirmation de votre adresse e-mail
                            </h2>
                            <p style="font-size: 13px; color: #475569; margin: 0 0 20px 0;">
                                Vous avez initié l'activation de votre organisation sur SenWiseTool. Pour vérifier votre adresse e-mail et passer à l'étape suivante, utilisez le code temporaire ci-dessous :
                            </p>

                            <!-- Boîte OTP -->
                            <div style="background-color: #f0fdf4; border: 2px dashed #86efac; border-radius: 12px; padding: 24px; text-align: center; margin: 24px 0;">
                                <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.5px; color: #15803d; margin-bottom: 8px;">
                                    Votre code de vérification à 6 chiffres
                                </div>
                                <div style="font-size: 36px; font-weight: 900; letter-spacing: 10px; color: #047857; font-family: 'Courier New', Courier, monospace;">
                                    {otp_code}
                                </div>
                                <div style="font-size: 12px; color: #166534; margin-top: 8px;">
                                    ⏱ Valide pendant <strong>{expiration_minutes} minutes</strong>
                                </div>
                            </div>

                            <p style="font-size: 12px; color: #64748b; margin: 20px 0 0 0;">
                                ⚠️ Si vous n'êtes pas à l'origine de cette demande, vous pouvez ignorer cet e-mail en toute sécurité. Ne communiquez jamais ce code à un tiers.
                            </p>
                        </td>
                    </tr>

                    <!-- Pied de page -->
                    <tr>
                        <td style="background-color: #f1f5f9; padding: 20px 36px; text-align: center; border-top: 1px solid #e2e8f0;">
                            <p style="font-size: 11px; color: #94a3b8; margin: 0;">
                                &copy; 2026 SenWiseTool Security Shield. Tous droits réservés.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
        """

        text_content = f"""
SenWiseTool - Code de sécurité OTP

Votre code de vérification à 6 chiffres est :
>>> {otp_code} <<<

Ce code expire dans {expiration_minutes} minutes.
Ne transmettez jamais ce code à personne.

© 2026 SenWiseTool Platform
        """

        return await self.send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content
        )

    async def send_revision_requested_email(
        self,
        to_email: str,
        org_name: str,
        review_notes: str,
        custom_questions: Optional[List[Dict[str, Any]]] = None,
        portal_url: Optional[str] = None
    ) -> bool:
        """
        Envoie un courriel officiel notifiant qu'une révision / complément d'information
        est requis pour l'organisation, avec la liste des questions et directives.
        """
        subject = f"[SenWiseTool] Complément d'information requis : dossier '{org_name}'"
        target_url = portal_url or f"{settings.FRONTEND_URL}/fr/onboarding/review"

        # Badge questions
        questions_html = ""
        questions_text = ""
        if custom_questions and len(custom_questions) > 0:
            q_items = ""
            for idx, q in enumerate(custom_questions, start=1):
                label = q.get("label", f"Question {idx}")
                q_type = q.get("type", "text")
                # Vérification exhaustive camelCase et snake_case pour garantir le respect de l'obligation
                is_req = q.get("is_required") if q.get("is_required") is not None else q.get("isRequired", True)
                req_badge = '<span style="color: #ef4444; font-size: 10px; font-weight: bold; background: #fee2e2; padding: 2px 6px; border-radius: 4px; margin-left: 6px;">Requis</span>' if is_req else '<span style="color: #64748b; font-size: 10px; background: #f1f5f9; padding: 2px 6px; border-radius: 4px; margin-left: 6px;">Optionnel</span>'
                q_items += f"""
                <li style="margin-bottom: 10px; padding: 10px 14px; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px;">
                    <div style="font-weight: 600; font-size: 13px; color: #1e293b;">{idx}. {label} {req_badge}</div>
                    <div style="font-size: 11px; color: #64748b; margin-top: 3px;">Type de réponse attendue : <strong>{q_type}</strong></div>
                </li>
                """
                questions_text += f"\n- {idx}. {label} ({'Requis' if is_req else 'Optionnel'}, type: {q_type})"

            questions_html = f"""
            <div style="margin: 24px 0;">
                <h4 style="font-size: 13px; font-weight: 700; color: #1e293b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px;">
                    Questions complémentaires posées par le Super Administrateur ({len(custom_questions)}) :
                </h4>
                <ul style="list-style: none; padding: 0; margin: 0;">
                    {q_items}
                </ul>
            </div>
            """

        html_content = f"""
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Complément d'information requis</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b; line-height: 1.6;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f8fafc; padding: 40px 15px;">
        <tr>
            <td align="center">
                <table width="600" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.05); border: 1px solid #e2e8f0;">
                    <!-- En-tête -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #07192c 0%, #0c2d4f 100%); padding: 32px 40px;">
                            <div style="font-size: 22px; font-weight: 800; color: #ffffff;">
                                SenWise<span style="color: #10b981;">Tool</span>
                            </div>
                            <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1.2px; color: #f59e0b; margin-top: 4px; font-weight: 700;">
                                Instruction d'Agrément • Révision Requise
                            </div>
                        </td>
                    </tr>

                    <!-- Corps -->
                    <tr>
                        <td style="padding: 40px;">
                            <h2 style="font-size: 18px; font-weight: 700; color: #0f172a; margin: 0 0 16px 0;">
                                Complément d'information pour « {org_name} »
                            </h2>

                            <p style="font-size: 14px; color: #475569; margin: 0 0 20px 0;">
                                Bonjour, le Super Administrateur de la plateforme <strong>SenWiseTool</strong> a examiné votre dossier d'onboarding et sollicite des précisions ou corrections avant de valider votre agrément.
                            </p>

                            <!-- Directives de révision -->
                            <div style="background-color: #fffbeb; border-left: 4px solid #f59e0b; padding: 16px 20px; border-radius: 8px; margin: 24px 0;">
                                <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: #b45309; letter-spacing: 0.5px; margin-bottom: 6px;">
                                    Directives de l'Administrateur :
                                </div>
                                <div style="font-size: 13px; color: #78350f; white-space: pre-wrap; line-height: 1.6;">
                                    {review_notes or "Merci de mettre à jour les éléments de votre dossier selon les questions ci-dessous."}
                                </div>
                            </div>

                            {questions_html}

                            <!-- Bouton CTA -->
                            <div style="text-align: center; margin: 32px 0;">
                                <a href="{target_url}" target="_blank" style="display: inline-block; background-color: #00a86b; background: linear-gradient(135deg, #00a86b 0%, #008f5a 100%); color: #ffffff; font-size: 14px; font-weight: 700; text-decoration: none; padding: 14px 32px; border-radius: 10px; box-shadow: 0 4px 12px rgba(0, 168, 107, 0.35);">
                                    Accéder au dossier & Compléter &rarr;
                                </a>
                            </div>

                            <p style="font-size: 12px; color: #64748b; margin: 24px 0 8px 0;">
                                Si le bouton ci-dessus ne fonctionne pas, copiez-collez l'URL suivante dans votre navigateur :
                            </p>
                            <div style="background-color: #f8fafc; border: 1px dashed #cbd5e1; padding: 10px 14px; border-radius: 8px; font-family: monospace; font-size: 11px; color: #334155; word-break: break-all;">
                                {target_url}
                            </div>
                        </td>
                    </tr>

                    <!-- Pied de page -->
                    <tr>
                        <td style="background-color: #f1f5f9; padding: 20px 40px; text-align: center; border-top: 1px solid #e2e8f0;">
                            <p style="font-size: 11px; color: #94a3b8; margin: 0;">
                                &copy; 2026 SenWiseTool Security & Compliance. Tous droits réservés.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
        """

        text_content = f"""
SenWiseTool - Complément d'information requis pour {org_name}

Bonjour,

Le Super Administrateur sollicite des précisions pour valider l'agrément de votre organisation.

Directives :
{review_notes}
{questions_text}

Pour mettre à jour votre dossier, connectez-vous sur :
{target_url}

© 2026 SenWiseTool Platform
        """

        return await self.send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content
        )

    async def send_organization_activated_email(
        self,
        to_email: str,
        org_name: str,
        org_code: str,
        portal_url: Optional[str] = None
    ) -> bool:
        """
        Envoie un courriel officiel confirmant que l'organisation a été validée
        par le Super Administrateur et qu'elle est désormais active et opérationnelle.
        """
        subject = f"[SenWiseTool] Agrément validé : Votre organisation '{org_name}' est opérationnelle !"
        target_url = portal_url or f"{settings.FRONTEND_URL}/dashboard"

        html_content = f"""
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Agrément validé - SenWiseTool</title>
</head>
<body style="margin: 0; padding: 0; background-color: #0f172a; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
    <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
        <tr>
            <td align="center" style="padding: 40px 10px;">
                <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.3);">
                    <!-- Header -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #064e3b 0%, #065f46 100%); padding: 36px 40px; text-align: left;">
                            <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                <tr>
                                    <td>
                                        <span style="font-size: 24px; font-weight: 800; color: #ffffff; letter-spacing: -0.5px;">SenWise<span style="color: #34d399;">Tool</span></span>
                                        <div style="font-size: 11px; font-weight: 700; color: #a7f3d0; text-transform: uppercase; letter-spacing: 1px; margin-top: 4px;">
                                            AGRÉMENT OFFICIEL VALIDÉ & ACCORDÉ
                                        </div>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <!-- Body -->
                    <tr>
                        <td style="padding: 40px 40px 32px 40px;">
                            <div style="display: inline-block; background-color: #ecfdf5; border: 1px solid #a7f3d0; color: #047857; font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 4px 10px; border-radius: 20px; margin-bottom: 20px;">
                                Dossier #{org_code} • Opérationnel
                            </div>

                            <h1 style="font-size: 20px; font-weight: 700; color: #0f172a; margin: 0 0 16px 0; line-height: 1.3;">
                                Félicitations ! Votre organisation « {org_name} » est officiellement active.
                            </h1>

                            <p style="font-size: 14px; line-height: 1.6; color: #334155; margin: 0 0 24px 0;">
                                Le Super Administrateur de la plateforme <strong>SenWiseTool</strong> a examiné et validé l'ensemble de votre dossier d'onboarding. Tous les flux de données, fonctionnalités cryptographiques et accès aux espaces d'administration sont désormais déverrouillés.
                            </p>

                            <!-- Call to Action -->
                            <div style="text-align: center; margin: 32px 0;">
                                <a href="{target_url}" style="display: inline-block; background: linear-gradient(135deg, #059669 0%, #10b981 100%); color: #ffffff; font-size: 14px; font-weight: 700; text-decoration: none; padding: 14px 32px; border-radius: 10px; box-shadow: 0 4px 6px -1px rgba(16, 185, 129, 0.4);">
                                    Accéder au tableau de bord &rarr;
                                </a>
                            </div>

                            <p style="font-size: 12px; color: #64748b; line-height: 1.5; margin-top: 24px;">
                                Vous pouvez dès à présent configurer vos capteurs IoT, inviter vos collaborateurs et démarrer l'exploitation de vos flux.
                            </p>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f1f5f9; padding: 20px 40px; text-align: center; border-top: 1px solid #e2e8f0;">
                            <p style="font-size: 11px; color: #94a3b8; margin: 0;">
                                &copy; 2026 SenWiseTool Security & Compliance. Tous droits réservés.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
        """

        text_content = f"""
Félicitations ! L'agrément de votre organisation '{org_name}' (#{org_code}) a été validé.
Votre organisation est désormais opérationnelle sur SenWiseTool.

Accédez à votre tableau de bord : {target_url}

© 2026 SenWiseTool Platform
        """

        return await self.send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content
        )

    async def send_organization_official_activation_email(
        self,
        to_email: str,
        org_name: str,
        org_code: str,
        admin_email: Optional[str] = None,
        attestation_url: Optional[str] = None,
        portal_url: Optional[str] = None
    ) -> bool:
        """
        Envoie un courriel institutionnel officiel confirmant l'agrément et l'homologation
        directement à l'adresse officielle de l'organisation (représentants légaux),
        avec lien vers l'attestation certifiée et le portail SenWiseTool.
        """
        subject = f"[SenWiseTool] Notification Officielle d'Agrément & Homologation : '{org_name}' (#{org_code})"
        target_attestation = attestation_url or f"{settings.FRONTEND_URL}/fr/verify/attestation?code={org_code}"
        target_portal = portal_url or f"{settings.FRONTEND_URL}/dashboard"

        admin_row = f"""
        <tr>
            <td style="padding: 10px 0; border-bottom: 1px solid #f1f5f9; color: #64748b; font-size: 13px;">Administrateur délégué :</td>
            <td style="padding: 10px 0; border-bottom: 1px solid #f1f5f9; color: #0f172a; font-size: 13px; font-weight: 600; text-align: right;">{admin_email}</td>
        </tr>
        """ if admin_email else ""

        html_content = f"""
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Homologation et Agrément Officiel - SenWiseTool</title>
</head>
<body style="margin: 0; padding: 0; background-color: #0f172a; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
    <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
        <tr>
            <td align="center" style="padding: 40px 10px;">
                <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.3);">
                    <!-- Header Officiel Institutionnel -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #022c22 0%, #064e3b 100%); padding: 36px 40px; text-align: left; border-bottom: 3px solid #10b981;">
                            <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                <tr>
                                    <td>
                                        <span style="font-size: 24px; font-weight: 800; color: #ffffff; letter-spacing: -0.5px;">SenWise<span style="color: #34d399;">Tool</span></span>
                                        <div style="font-size: 10px; font-weight: 700; color: #6ee7b7; text-transform: uppercase; letter-spacing: 1.5px; margin-top: 4px;">
                                            DIRECTION DE LA GOUVERNANCE & DE LA CONFORMITÉ
                                        </div>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <!-- Body -->
                    <tr>
                        <td style="padding: 40px 40px 32px 40px;">
                            <div style="display: inline-block; background-color: #ecfdf5; border: 1px solid #a7f3d0; color: #047857; font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 4px 12px; border-radius: 20px; margin-bottom: 20px;">
                                Décision Officielle d'Agrément • Matricule #{org_code}
                            </div>

                            <h1 style="font-size: 20px; font-weight: 700; color: #0f172a; margin: 0 0 16px 0; line-height: 1.3;">
                                Notification d'homologation officielle de votre organisation
                            </h1>

                            <p style="font-size: 14px; line-height: 1.6; color: #334155; margin: 0 0 18px 0;">
                                À l'attention de la Direction et des Représentants Légaux de <strong>{org_name}</strong>,
                            </p>

                            <p style="font-size: 14px; line-height: 1.6; color: #334155; margin: 0 0 24px 0;">
                                Nous vous notifions officiellement que la demande d'agrément et le dossier d'onboarding déposés pour votre organisation <strong>« {org_name} »</strong> ont été instruits avec avis favorable et validés par le Super Administrateur de <strong>SenWiseTool</strong>.
                            </p>

                            <!-- Carte institutionnelle du dossier -->
                            <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; margin-bottom: 24px;">
                                <table border="0" cellpadding="0" cellspacing="0" width="100%">
                                    <tr>
                                        <td style="padding: 10px 0; border-bottom: 1px solid #f1f5f9; color: #64748b; font-size: 13px;">Dénomination homologuée :</td>
                                        <td style="padding: 10px 0; border-bottom: 1px solid #f1f5f9; color: #0f172a; font-size: 13px; font-weight: 700; text-align: right;">{org_name}</td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 10px 0; border-bottom: 1px solid #f1f5f9; color: #64748b; font-size: 13px;">Matricule officiel :</td>
                                        <td style="padding: 10px 0; border-bottom: 1px solid #f1f5f9; color: #047857; font-size: 13px; font-weight: 700; font-family: monospace; text-align: right;">{org_code}</td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 10px 0; border-bottom: 1px solid #f1f5f9; color: #64748b; font-size: 13px;">Statut juridique de conformité :</td>
                                        <td style="padding: 10px 0; border-bottom: 1px solid #f1f5f9; color: #047857; font-size: 13px; font-weight: 700; text-align: right;">AGRÉÉE & OPÉRATIONNELLE</td>
                                    </tr>
                                    {admin_row}
                                    <tr>
                                        <td style="padding: 10px 0; color: #64748b; font-size: 13px;">Attestation officielle :</td>
                                        <td style="padding: 10px 0; color: #0f172a; font-size: 13px; font-weight: 600; text-align: right;">Scellé SHA-256 avec QR Code</td>
                                    </tr>
                                </table>
                            </div>

                            <p style="font-size: 13px; line-height: 1.6; color: #475569; margin: 0 0 24px 0;">
                                Votre attestation officielle d'agrément est disponible et vérifiable publiquement par vos partenaires grâce au QR Code cryptographique présent sur le document.
                            </p>

                            <!-- Boutons d'action -->
                            <div style="text-align: center; margin: 28px 0;">
                                <a href="{target_attestation}" style="display: inline-block; background: linear-gradient(135deg, #059669 0%, #10b981 100%); color: #ffffff; font-size: 14px; font-weight: 700; text-decoration: none; padding: 14px 28px; border-radius: 10px; box-shadow: 0 4px 6px -1px rgba(16, 185, 129, 0.4); margin-bottom: 12px;">
                                    Consulter l'attestation officielle &rarr;
                                </a>
                                <br />
                                <a href="{target_portal}" style="display: inline-block; color: #047857; font-size: 12px; font-weight: 600; text-decoration: underline; padding: 6px 12px;">
                                    Accéder au portail SenWiseTool
                                </a>
                            </div>

                            <p style="font-size: 11px; color: #94a3b8; line-height: 1.5; margin-top: 24px; border-top: 1px solid #f1f5f9; padding-top: 16px;">
                                Cette notification institutionnelle atteste de l'homologation de votre entité. Pour toute modification légale ou administrative future, veuillez contacter la direction de conformité SenWiseTool.
                            </p>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f1f5f9; padding: 20px 40px; text-align: center; border-top: 1px solid #e2e8f0;">
                            <p style="font-size: 11px; color: #94a3b8; margin: 0;">
                                &copy; 2026 SenWiseTool Security & Compliance Department. Document certifié conforme.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
        """

        text_content = f"""
Notification Officielle d'Agrément & Homologation - SenWiseTool

À l'attention de la Direction de {org_name},

Nous vous confirmons que votre organisation '{org_name}' (Matricule : {org_code}) a été officiellement homologuée et activée sur la plateforme SenWiseTool par le Super Administrateur.

Statut : HOMOLOGUÉ & ACTIF
{f"Administrateur désigné : {admin_email}" if admin_email else ""}

Consultez et téléchargez votre attestation officielle avec QR Code : {target_attestation}
Accédez à l'espace de votre organisation : {target_portal}

© 2026 SenWiseTool Security & Compliance Department
        """

        return await self.send_email(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content
        )

    async def send_organization_suspended_email(
        self,
        to_email: str,
        org_name: str,
        reason: str,
        portal_url: Optional[str] = None
    ) -> bool:
        """
        Envoie un courriel officiel notifiant la suspension temporaire de l'organisation
        avec la justification détaillée.
        """
        subject = f"[SenWiseTool] Notification de suspension temporaire : '{org_name}'"
        target_url = portal_url or f"{settings.FRONTEND_URL}/dashboard"

        html_content = f"""
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <title>Suspension temporaire - SenWiseTool</title>
</head>
<body style="margin: 0; padding: 0; background-color: #0f172a; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
    <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
        <tr>
            <td align="center" style="padding: 40px 10px;">
                <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.3);">
                    <tr>
                        <td style="background: linear-gradient(135deg, #78350f 0%, #b45309 100%); padding: 36px 40px; text-align: left;">
                            <span style="font-size: 24px; font-weight: 800; color: #ffffff;">SenWise<span style="color: #fbbf24;">Tool</span></span>
                            <div style="font-size: 11px; font-weight: 700; color: #fde68a; text-transform: uppercase; letter-spacing: 1px; margin-top: 4px;">
                                NOTIFICATION DE SUSPENSION TEMPORAIRE
                            </div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 40px 40px 32px 40px;">
                            <h1 style="font-size: 18px; font-weight: 700; color: #0f172a; margin: 0 0 16px 0;">
                                Suspension temporaire de « {org_name} »
                            </h1>
                            <p style="font-size: 14px; line-height: 1.6; color: #334155; margin: 0 0 20px 0;">
                                Le Super Administrateur a suspendu temporairement les accès et les flux de votre organisation pour le motif suivant :
                            </p>
                            <div style="background-color: #fffbeb; border-left: 4px solid #f59e0b; padding: 16px; border-radius: 4px; margin-bottom: 24px; font-size: 13px; line-height: 1.6; color: #92400e;">
                                {reason}
                            </div>
                            <p style="font-size: 12px; color: #64748b; line-height: 1.5;">
                                Pour tout recours ou mise en conformité, veuillez contacter le support administratif de la plateforme.
                            </p>
                        </td>
                    </tr>
                    <tr>
                        <td style="background-color: #f1f5f9; padding: 20px 40px; text-align: center; border-top: 1px solid #e2e8f0;">
                            <p style="font-size: 11px; color: #94a3b8; margin: 0;">&copy; 2026 SenWiseTool Security & Compliance.</p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
        """

        text_content = f"""
Notification de suspension temporaire pour {org_name}.

Motif de suspension :
{reason}

© 2026 SenWiseTool Platform
        """

        return await self.send_email(to_email=to_email, subject=subject, html_content=html_content, text_content=text_content)

    async def send_organization_rejected_email(
        self,
        to_email: str,
        org_name: str,
        reason: str,
        portal_url: Optional[str] = None
    ) -> bool:
        """
        Envoie un courriel officiel notifiant le rejet définitif du dossier d'onboarding
        avec les motifs argumentés.
        """
        subject = f"[SenWiseTool] Décision d'instruction : Rejet du dossier '{org_name}'"

        html_content = f"""
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <title>Rejet de dossier - SenWiseTool</title>
</head>
<body style="margin: 0; padding: 0; background-color: #0f172a; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
    <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
        <tr>
            <td align="center" style="padding: 40px 10px;">
                <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.3);">
                    <tr>
                        <td style="background: linear-gradient(135deg, #881337 0%, #be123c 100%); padding: 36px 40px; text-align: left;">
                            <span style="font-size: 24px; font-weight: 800; color: #ffffff;">SenWise<span style="color: #fda4af;">Tool</span></span>
                            <div style="font-size: 11px; font-weight: 700; color: #fecdd3; text-transform: uppercase; letter-spacing: 1px; margin-top: 4px;">
                                DÉCISION D'INSTRUCTION • REJET
                            </div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 40px 40px 32px 40px;">
                            <h1 style="font-size: 18px; font-weight: 700; color: #0f172a; margin: 0 0 16px 0;">
                                Rejet du dossier pour « {org_name} »
                            </h1>
                            <p style="font-size: 14px; line-height: 1.6; color: #334155; margin: 0 0 20px 0;">
                                À la suite de l'examen de conformité, le Super Administrateur n'a pas pu accorder l'agrément à votre organisation pour le motif suivant :
                            </p>
                            <div style="background-color: #fff1f2; border-left: 4px solid #f43f5e; padding: 16px; border-radius: 4px; margin-bottom: 24px; font-size: 13px; line-height: 1.6; color: #9f1239;">
                                {reason}
                            </div>
                        </td>
                    </tr>
                    <tr>
                        <td style="background-color: #f1f5f9; padding: 20px 40px; text-align: center; border-top: 1px solid #e2e8f0;">
                            <p style="font-size: 11px; color: #94a3b8; margin: 0;">&copy; 2026 SenWiseTool Security & Compliance.</p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
        """

        text_content = f"""
Décision d'instruction - Rejet du dossier {org_name}.

Motif de rejet :
{reason}

© 2026 SenWiseTool Platform
        """

        return await self.send_email(to_email=to_email, subject=subject, html_content=html_content, text_content=text_content)

    async def send_onboarding_submission_receipt_email(
        self,
        to_email: str,
        org_name: str,
        org_code: str,
        submitted_at_str: str,
        portal_url: Optional[str] = None,
        attestation_url: Optional[str] = None
    ) -> bool:
        """
        Envoie l'accusé de réception officiel de dépôt de dossier d'organisation
        avec les références d'enregistrement et le lien vers l'attestation scellée.
        """
        subject = f"[SenWiseTool] Accusé de réception officiel de dépôt de dossier : '{org_name}' ({org_code})"
        target_portal = portal_url or f"{settings.FRONTEND_URL}/fr/onboarding/pending"
        target_attestation = attestation_url or f"{settings.FRONTEND_URL}/fr/verify/attestation?code={org_code}"

        html_content = f"""
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <title>Accusé de réception - Dossier d'organisation</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px; color: #1e293b;">
    <table width="100%" cellpadding="0" cellspacing="0" style="max-width: 620px; margin: 0 auto; background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.06); border: 1px solid #e2e8f0;">
        <tr>
            <td style="background: linear-gradient(135deg, #059669 0%, #10b981 100%); padding: 36px 32px; text-align: center;">
                <div style="background: rgba(255,255,255,0.2); width: 64px; height: 64px; border-radius: 50%; margin: 0 auto 16px; line-height: 64px; font-size: 32px; color: #ffffff;">
                    ✓
                </div>
                <h1 style="color: #ffffff; margin: 0 0 8px; font-size: 22px; font-weight: 700; letter-spacing: -0.5px;">
                    Dépôt de Dossier Validé avec Succès
                </h1>
                <p style="color: rgba(255,255,255,0.9); margin: 0; font-size: 14px;">
                    Accusé de réception officiel • Enregistrement réglementaire
                </p>
            </td>
        </tr>
        <tr>
            <td style="padding: 32px;">
                <p style="font-size: 15px; line-height: 1.6; margin-top: 0; color: #334155;">
                    Bonjour,
                </p>
                <p style="font-size: 14px; line-height: 1.6; color: #334155;">
                    Nous vous confirmons la bonne réception de la constitution du dossier d'inscription pour l'organisation <strong>{org_name}</strong>.
                    Votre dossier a été scellé et placé en enclave d'audit réglementaire sous l'autorité du Super Administrateur de la plateforme <strong>SenWiseTool</strong>.
                </p>

                <!-- Récapitulatif Box -->
                <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; margin: 24px 0;">
                    <h3 style="margin-top: 0; margin-bottom: 14px; font-size: 13px; font-weight: 700; color: #0f172a; text-transform: uppercase; letter-spacing: 0.5px;">
                        Bordereau officiel d'enregistrement
                    </h3>
                    <table width="100%" cellpadding="6" cellspacing="0" style="font-size: 13px; color: #334155;">
                        <tr>
                            <td style="color: #64748b; width: 45%;">Dénomination :</td>
                            <td style="font-weight: 600; color: #0f172a;">{org_name}</td>
                        </tr>
                        <tr>
                            <td style="color: #64748b;">Matricule d'inscription :</td>
                            <td style="font-weight: 700; color: #059669; font-family: monospace;">{org_code}</td>
                        </tr>
                        <tr>
                            <td style="color: #64748b;">Horodatage du dépôt :</td>
                            <td style="font-weight: 500;">{submitted_at_str}</td>
                        </tr>
                        <tr>
                            <td style="color: #64748b;">Statut d'instruction :</td>
                            <td><span style="background: #ecfdf5; color: #065f46; font-weight: 600; padding: 3px 8px; border-radius: 6px; font-size: 11px;">En cours d'examen</span></td>
                        </tr>
                    </table>
                </div>

                <div style="text-align: center; margin: 32px 0 20px;">
                    <a href="{target_portal}" style="display: inline-block; background-color: #059669; color: #ffffff; text-decoration: none; padding: 14px 28px; border-radius: 10px; font-weight: 600; font-size: 14px; box-shadow: 0 4px 6px -1px rgba(5, 150, 105, 0.2);">
                        Suivre l'instruction & Télécharger l'attestation
                    </a>
                </div>

                <p style="font-size: 12px; color: #64748b; text-align: center; margin: 12px 0 24px;">
                    L'attestation officielle avec QR Code de certification d'authenticité est accessible directement depuis votre espace de suivi ou via <a href="{target_attestation}" style="color: #059669; text-decoration: underline;">ce lien sécurisé</a>.
                </p>

                <div style="background-color: #eff6ff; border-left: 4px solid #3b82f6; padding: 14px; border-radius: 6px; font-size: 12px; line-height: 1.5; color: #1e40af;">
                    <strong>Prochaines étapes :</strong> Le délai moyen d'instruction par le Super Administrateur est de 24h à 48h ouvrées. Si des pièces complémentaires ou des précisions sont nécessaires, vous recevrez une notification par email pour y répondre directement depuis la plateforme.
                </div>
            </td>
        </tr>
        <tr>
            <td style="background-color: #f1f5f9; padding: 20px 32px; text-align: center; border-top: 1px solid #e2e8f0;">
                <p style="font-size: 12px; color: #64748b; margin: 0 0 4px;">
                    SenWiseTool Compliance & Organization Registry Engine
                </p>
                <p style="font-size: 11px; color: #94a3b8; margin: 0;">
                    Ce message automatique confirme le scellement officiel de votre dossier d'agrément.
                </p>
            </td>
        </tr>
    </table>
</body>
</html>
        """

        text_content = f"""
ACCUSÉ DE RÉCEPTION OFFICIEL - SENWISETOOL
Dépôt de dossier d'organisation : {org_name} ({org_code})

Bonjour,

Nous vous confirmons la bonne réception de la constitution du dossier d'inscription pour l'organisation '{org_name}'.
Votre dossier a été scellé et placé en cours d'instruction réglementaire sous l'autorité du Super Administrateur de la plateforme SenWiseTool.

BORDEREAU D'ENREGISTREMENT :
- Organisation : {org_name}
- Matricule unique : {org_code}
- Horodatage officiel : {submitted_at_str}
- Statut : En cours d'examen

Vous pouvez consulter votre espace de suivi et télécharger votre attestation de dépôt officielle avec QR Code d'authenticité sur :
{target_portal}

Vérification d'authenticité directe :
{target_attestation}

Le délai d'instruction moyen est de 24h à 48h ouvrées.

Cordialement,
SenWiseTool Compliance Team
        """

        return await self.send_email(to_email=to_email, subject=subject, html_content=html_content, text_content=text_content)

    async def send_member_invitation_email(
        self,
        to_email: str,
        member_name: Optional[str],
        org_name: str,
        role_name: str,
        invite_url: str,
        personal_message: Optional[str] = None,
        mfa_required: bool = False
    ) -> bool:
        """
        Envoie un courriel d'invitation à un collaborateur pour rejoindre l'organisation.
        """
        subject = f"Invitation à rejoindre l'organisation {org_name} sur SenWiseTool"
        salutation = f"Bonjour {member_name}," if member_name else "Bonjour,"

        message_html = ""
        if personal_message and personal_message.strip():
            message_html = f"""
            <div style="background-color: #f1f5f9; border-left: 4px solid #10b981; padding: 14px 18px; margin: 20px 0; border-radius: 0 8px 8px 0;">
                <p style="margin: 0; font-size: 13px; color: #334155; font-style: italic;">
                    &laquo; {personal_message.strip()} &raquo;
                </p>
            </div>
            """

        mfa_notice = ""
        if mfa_required:
            mfa_notice = """
            <p style="font-size: 12px; color: #0284c7; background-color: #f0f9ff; border: 1px solid #bae6fd; padding: 10px 14px; border-radius: 8px; margin: 16px 0;">
                <strong>Sécurité renforcée :</strong> L'authentification multi-facteurs (MFA) est requise pour ce compte. Vous serez invité à la configurer lors de votre première connexion.
            </p>
            """

        html_content = f"""
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <title>{subject}</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 40px 20px; color: #1e293b;">
    <table align="center" border="0" cellpadding="0" cellspacing="0" width="600" style="background-color: #ffffff; border-radius: 16px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
        <tr>
            <td style="background: linear-gradient(135deg, #041329 0%, #07192C 100%); padding: 32px 40px; text-align: center;">
                <h1 style="color: #ffffff; font-size: 22px; font-weight: 700; margin: 0 0 6px; letter-spacing: -0.02em;">
                    SenWiseTool
                </h1>
                <p style="color: #10b981; font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; margin: 0;">
                    Invitation à rejoindre un espace de travail
                </p>
            </td>
        </tr>
        <tr>
            <td style="padding: 40px;">
                <p style="font-size: 15px; line-height: 24px; color: #334155; margin: 0 0 16px;">
                    {salutation}
                </p>
                <p style="font-size: 14px; line-height: 22px; color: #475569; margin: 0 0 20px;">
                    Vous avez été invité(e) à intégrer l'organisation <strong>{org_name}</strong> sur la plateforme de données et de télémétrie <strong>SenWiseTool</strong> avec le profil opérationnel suivant :
                </p>

                <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 18px 24px; margin-bottom: 24px;">
                    <table width="100%" cellpadding="4" cellspacing="0">
                        <tr>
                            <td width="35%" style="font-size: 12px; color: #64748b; font-weight: 600;">Organisation :</td>
                            <td style="font-size: 13px; color: #0f172a; font-weight: 700;">{org_name}</td>
                        </tr>
                        <tr>
                            <td style="font-size: 12px; color: #64748b; font-weight: 600;">Rôle attribué :</td>
                            <td style="font-size: 13px; color: #10b981; font-weight: 700;">{role_name}</td>
                        </tr>
                        <tr>
                            <td style="font-size: 12px; color: #64748b; font-weight: 600;">Adresse e-mail :</td>
                            <td style="font-size: 13px; color: #0f172a; font-mono font-weight: 600;">{to_email}</td>
                        </tr>
                    </table>
                </div>

                {message_html}
                {mfa_notice}

                <div style="text-align: center; margin: 32px 0;">
                    <a href="{invite_url}" style="background-color: #10b981; color: #ffffff; padding: 14px 32px; font-size: 14px; font-weight: 600; text-decoration: none; border-radius: 10px; display: inline-block; box-shadow: 0 2px 4px rgba(16, 185, 129, 0.25);">
                        Accepter l'invitation &amp; Créer mon mot de passe
                    </a>
                </div>

                <p style="font-size: 12px; color: #94a3b8; line-height: 18px; margin: 24px 0 0;">
                    Si le bouton ne fonctionne pas, vous pouvez copier et coller ce lien dans votre navigateur :<br>
                    <a href="{invite_url}" style="color: #10b981; word-break: break-all;">{invite_url}</a>
                </p>
            </td>
        </tr>
        <tr>
            <td style="background-color: #f8fafc; border-top: 1px solid #e2e8f0; padding: 20px 40px; text-align: center;">
                <p style="font-size: 11px; color: #94a3b8; margin: 0;">
                    SenWiseTool Telemetry & Data Platform • Cet e-mail d'invitation est sécurisé et personnel.
                </p>
            </td>
        </tr>
    </table>
</body>
</html>
        """

        text_content = f"""
INVITATION SENWISETOOL - REJOINDRE L'ORGANISATION {org_name}

{salutation}

Vous avez été invité(e) à intégrer l'organisation '{org_name}' sur la plateforme SenWiseTool.

DÉTAILS DU PROFIL :
- Organisation : {org_name}
- Rôle attribué : {role_name}
- Adresse e-mail : {to_email}

{f"Message de l'administrateur : '{personal_message}'" if personal_message else ""}

Pour accepter cette invitation et définir votre mot de passe d'accès, cliquez sur le lien ci-dessous :
{invite_url}

Cordialement,
L'équipe SenWiseTool
        """

        return await self.send_email(to_email=to_email, subject=subject, html_content=html_content, text_content=text_content)

email_service = EmailService()
