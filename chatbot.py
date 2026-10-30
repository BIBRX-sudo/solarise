import logging
from groq_service import (
    get_ai_solar_reply,
    is_groq_configured,
)

logger = logging.getLogger(__name__)

MENU_REPLY = (
    "Namaskar! Welcome to Solarise Odisha ☀️\n"
    "Under ADP Green Energy Solutions.\n\n"
    "How can we help you?\n\n"
    "1. Rooftop solar enquiry\n"
    "2. PM Surya Ghar subsidy\n"
    "3. Solar cost and savings\n"
    "4. Installation process\n"
    "5. Net metering\n"
    "6. Maintenance and support\n"
    "7. Talk to our team\n\n"
    "Reply with a number or type your question."
)


def get_deterministic_reply(text: str) -> str | None:
    """Handles exact menu commands and numbers deterministically."""
    if text in ["hi", "hello", "hey", "namaste", "namaskar", "menu"]:
        return MENU_REPLY

    if text == "1":
        return (
            "Let's explore rooftop solar for your property! ☀️\n\n"
            "Is this for a home or a business?\n"
            "Reply HOME or BUSINESS."
        )

    if text == "2":
        return (
            "PM Surya Ghar is a government rooftop solar scheme "
            "for eligible residential households.\n\n"
            "The central subsidy can be up to ₹78,000 for "
            "eligible systems, subject to current scheme rules.\n\n"
            "Official portal: https://pmsuryaghar.gov.in/\n\n"
            "Reply 7 if you would like to speak with our team."
        )

    if text == "3":
        return (
            "Solar system cost and savings depend on your "
            "electricity consumption, roof conditions and system size.\n\n"
            "To request an estimate, please reply 7 to speak "
            "with the Solarise Odisha team."
        )

    if text == "4":
        return (
            "A typical rooftop solar project involves:\n\n"
            "1. Initial enquiry\n"
            "2. Roof and electricity usage assessment\n"
            "3. System design and quotation\n"
            "4. Installation and required approvals\n"
            "5. System commissioning\n\n"
            "Exact steps depend on your property and applicable rules."
        )

    if text == "5":
        return (
            "Net metering measures electricity imported from "
            "and exported to the grid, where applicable.\n\n"
            "Your DISCOM's rules and approvals determine "
            "the arrangement for your property."
        )

    if text == "6":
        return (
            "We can help route your maintenance or warranty enquiry.\n\n"
            "Please reply 7 to request assistance from our team."
        )

    if text == "7":
        return (
            "Thank you for your interest in Solarise Odisha! ☀️\n\n"
            "Our next step is to collect your contact details "
            "and solar requirements.\n\n"
            "Lead collection will be enabled in the next step."
        )

    return None


def get_static_keyword_reply(text: str) -> str | None:
    """Legacy keyword matching used as a fallback when AI is unconfigured or offline."""
    if "rooftop solar" in text:
        return (
            "Let's explore rooftop solar for your property! ☀️\n\n"
            "Is this for a home or a business?\n"
            "Reply HOME or BUSINESS."
        )

    if "subsidy" in text:
        return (
            "PM Surya Ghar is a government rooftop solar scheme "
            "for eligible residential households.\n\n"
            "The central subsidy can be up to ₹78,000 for "
            "eligible systems, subject to current scheme rules.\n\n"
            "Official portal: https://pmsuryaghar.gov.in/\n\n"
            "Reply 7 if you would like to speak with our team."
        )

    if any(word in text for word in ["cost", "price", "savings", "bill"]):
        return (
            "Solar system cost and savings depend on your "
            "electricity consumption, roof conditions and system size.\n\n"
            "To request an estimate, please reply 7 to speak "
            "with the Solarise Odisha team."
        )

    if "installation" in text:
        return (
            "A typical rooftop solar project involves:\n\n"
            "1. Initial enquiry\n"
            "2. Roof and electricity usage assessment\n"
            "3. System design and quotation\n"
            "4. Installation and required approvals\n"
            "5. System commissioning\n\n"
            "Exact steps depend on your property and applicable rules."
        )

    if "net metering" in text:
        return (
            "Net metering measures electricity imported from "
            "and exported to the grid, where applicable.\n\n"
            "Your DISCOM's rules and approvals determine "
            "the arrangement for your property."
        )

    if any(word in text for word in ["maintenance", "warranty", "repair"]):
        return (
            "We can help route your maintenance or warranty enquiry.\n\n"
            "Please reply 7 to request assistance from our team."
        )

    if any(word in text for word in ["human", "agent", "consultation", "contact"]):
        return (
            "Thank you for your interest in Solarise Odisha! ☀️\n\n"
            "Our next step is to collect your contact details "
            "and solar requirements.\n\n"
            "Lead collection will be enabled in the next step."
        )

    return None


def get_reply(message: str) -> str:
    text = message.strip().lower()

    # 1. Exact deterministic menu options and greetings
    deterministic = get_deterministic_reply(text)
    if deterministic is not None:
        return deterministic

    # 2. Conversational AI via Groq if configured
    if is_groq_configured():
        return get_ai_solar_reply(message)

    # 3. Static fallback when AI is unconfigured or offline
    keyword_match = get_static_keyword_reply(text)
    if keyword_match is not None:
        return keyword_match

    return (
        "Sorry, I couldn't identify that question.\n\n"
        "Type MENU to see the available solar services."
    )
