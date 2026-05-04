from core.chat_reasoning import (
    build_compound_message_analysis_source,
    split_message_utterances,
)


def test_split_message_utterances_keeps_decimal_values() -> None:
    message = "O calado e 9.5m. Posso atracar na Secil? E para quinta?"

    assert split_message_utterances(message) == [
        "O calado e 9.5m.",
        "Posso atracar na Secil?",
        "E para quinta?",
    ]


def test_compound_message_analysis_extracts_context_before_question() -> None:
    message = (
        "O navio chega amanhã às 10h. Calado 9.5m. "
        "Preciso de saber se o cais TMS1 está livre e qual a previsão de vento para essa hora?"
    )

    source = build_compound_message_analysis_source(message)

    assert source is not None
    assert source["retrieval_mode"] == "message_analysis"
    snippet = source["snippet"]
    assert "1. (contexto) O navio chega amanhã às 10h." in snippet
    assert "2. (contexto) Calado 9.5m." in snippet
    assert "3. (pergunta) Preciso de saber se o cais TMS1 está livre" in snippet
    assert "Cais/terminal referido: TMS1." in snippet
    assert "Calado: 9,5 m." in snippet
    assert "Hora planeada/referida: 10:00." in snippet
    assert "Data relativa referida: amanhã." in snippet


def test_compound_message_analysis_lists_all_explicit_questions() -> None:
    source = build_compound_message_analysis_source("Previsão para amanhã? E para quinta?")

    assert source is not None
    assert (
        "Perguntas explicitas a responder: Previsão para amanhã? | E para quinta?"
        in source["snippet"]
    )


def test_compound_message_analysis_keeps_cancellation_context() -> None:
    source = build_compound_message_analysis_source("Cancelaram a manobra. Quando será a próxima?")

    assert source is not None
    assert "Contexto referido: manobra cancelada/abortada." in source["snippet"]
    assert "2. (pergunta) Quando será a próxima?" in source["snippet"]
