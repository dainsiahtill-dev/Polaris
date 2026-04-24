from polaris.cells.llm.control_plane.internal.tag_parser import create_tag_parser


def test_parser_handles_partial_tag_content_without_stalling():
    parser = create_tag_parser(flush_threshold=5)

    first_events = parser.process_chunk("<thinking>abc")
    first_types = [event.type for event in first_events]
    assert "thinking_start" in first_types

    second_events = parser.process_chunk("de</thinking>")
    second_types = [event.type for event in second_events]
    assert "thinking_chunk" in second_types
    assert "thinking_end" in second_types


def test_parser_switches_from_thinking_to_answer_across_chunks():
    parser = create_tag_parser(flush_threshold=5)

    first_events = parser.process_chunk("<thinking>abc</thinking><answer>de")
    first_types = [event.type for event in first_events]
    assert "thinking_start" in first_types
    assert "thinking_end" in first_types
    assert "answer_start" in first_types

    second_events = parser.process_chunk("f</answer>")
    second_types = [event.type for event in second_events]
    assert "answer_chunk" in second_types
    assert "answer_end" in second_types

    answer_chunks = [
        str(event.data.get("content", "")) for event in first_events + second_events if event.type == "answer_chunk"
    ]
    assert "".join(answer_chunks) == "def"
    assert "<answer>" not in "".join(answer_chunks)


def test_parser_keeps_partial_close_tag_in_buffer():
    parser = create_tag_parser(flush_threshold=5)

    first_events = parser.process_chunk("<answer>hello")
    mid_events = parser.process_chunk(" world</ans")
    end_events = parser.process_chunk("wer>")

    combined = first_events + mid_events + end_events
    answer_chunks = [str(event.data.get("content", "")) for event in combined if event.type == "answer_chunk"]
    answer_text = "".join(answer_chunks)
    assert answer_text == "hello world"
    assert "</ans" not in answer_text
