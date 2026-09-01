<instruction>
Split the input text into sentences by inserting <br> tags at sentence boundaries.

Rules:
1. Never add, delete, modify, or reorder any words. Only insert <br>.
2. Maximum sentence length (characters, excluding spaces and <br>):
   - CJK languages (Chinese, Japanese, Korean): {max_chars_cjk}
   - All other languages (English, Portuguese, Russian, Hebrew, Arabic, etc.): {max_chars_latin}
3. Break at semantically complete clause boundaries, preferring natural speech pauses.
4. Keep the original spaces between words unchanged. Do not merge or reflow.
5. Output only the processed text. No explanations, quotes, or code fences.
</instruction>

<example>
input: the quick brown fox jumps over the lazy dog again and again until sunset
output: the quick brown fox jumps over the lazy dog<br>again and again until sunset
</example>
