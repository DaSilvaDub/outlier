
import json
from outlier_scrapers import runner_common as rc

def group_outcomes_by_event(outcome_to_event, all_outcomes):
    event_to_outcomes = {}
    for outcome in all_outcomes:
        event = outcome_to_event.get(outcome, "UNKNOWN")
        event_to_outcomes.setdefault(event, set()).add(outcome)
    return event_to_outcomes

def pack_events_into_chunks(event_to_outcomes, chunk_size):
    chunks = []
    current_chunk = set()
    current_size = 0
    
    for event, outcomes in event_to_outcomes.items():
        if current_size + len(outcomes) > chunk_size and current_size > 0:
            chunks.append(current_chunk)
            current_chunk = set()
            current_size = 0
        current_chunk.update(outcomes)
        current_size += len(outcomes)
        
    if current_chunk:
        chunks.append(current_chunk)
    return chunks

print(pack_events_into_chunks({"E1": {"o1", "o2"}, "E2": {"o3"}, "E3": {"o4", "o5", "o6"}}, 2))

