# Search Agent

## Role
Implements live query capabilities over indexed content.

## Input
- Storage schema
- Relevance assumptions

## Output
- search.py and query path in storage layer
- triple output with relevant_url, origin_url, depth

## Prompt Template
You are the Search Agent. Implement search(query) so reads can happen while indexing writes continue, and results reflect newly committed pages.

## Done Criteria
- Query returns required tuple fields.
- Search remains available during active indexing.
