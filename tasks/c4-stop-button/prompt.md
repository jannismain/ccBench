For the c4-genai-suite, we received the following feature request:

<feature_request>
Title: Add a button to cancel the current answer

We need a way to cancel the current answer generation.
This is useful in the case that the LLM generates a long answer and the user notices that a crucial detail is missing from the prompt. Or when a parameter like the temperature was accidentally set to a high value such that the answer becomes a very long string of gibberish.

An idea would be that the send button becomes a stop button, when the LLM is generating an answer.
Pressing the stop button should cancel the current generation. A page refresh should still show the partially generated answer up to the point where the generation was stopped.
</feature_request>

Implement this feature in the c4-genai-suite. Create test cases to verify that the cancel button works as expected.
