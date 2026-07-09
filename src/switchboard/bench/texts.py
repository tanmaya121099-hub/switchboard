"""Benchmark corpus.

Texts mirror what the e-commerce agent actually says on calls, so measured
latency reflects the production workload — not lorem ipsum. Hinglish cases
exist because India-market deployments are code-switched by default, and no
provider publishes latency/quality numbers for them.
"""

TEXTS: dict[str, str] = {
    "short": "Your order has been confirmed.",
    "medium": (
        "Hi, this is Asha calling from QuickKart about the order you placed "
        "yesterday. I just need thirty seconds to confirm your cash on "
        "delivery order before we ship it."
    ),
    "long": (
        "Thank you for confirming. Your order number eight four seven two "
        "nine one contains two items: a pair of running shoes and a steel "
        "water bottle, with a total of one thousand four hundred and ninety "
        "nine rupees payable as cash on delivery. Our delivery partner will "
        "arrive on Thursday between two and six in the evening. Please keep "
        "the exact amount ready, and you can reschedule any time by calling "
        "this number back. Is there anything else I can help you with today?"
    ),
    "hinglish_short": "Aapka order confirm ho gaya hai, Thursday tak deliver hoga.",
    "hinglish_medium": (
        "Namaste! Main QuickKart se Asha bol rahi hoon. Aapne kal jo order "
        "kiya tha, cash on delivery, usko confirm karna tha. Kya aap "
        "Thursday ko delivery ke liye ghar par rahenge?"
    ),
    "numbers": (
        "Your order number is eight four seven two nine one, total amount "
        "one thousand four hundred ninety nine rupees, arriving Thursday "
        "between 2 PM and 6 PM."
    ),
}

HINGLISH_IDS = {"hinglish_short", "hinglish_medium"}
