# Guide to Prompt Engineering

Prompt engineering is the practice of designing and refining the inputs provided to large language models in order to elicit the most useful, accurate, and relevant outputs. As LLMs have become central components of software systems, the ability to craft effective prompts has evolved from an informal skill into a disciplined engineering practice with established patterns, anti-patterns, and evaluation methodologies. This guide provides a comprehensive overview of the key concepts, techniques, and best practices that practitioners should understand when working with modern language models.

## The Role of System Prompts

The system prompt is the foundational element of any LLM-based application. It establishes the model's persona, defines its capabilities and constraints, and sets the tone for all subsequent interactions. Crafting effective system prompts requires a careful balance between specificity and flexibility: a prompt that is too vague will produce inconsistent results, while one that is too rigid may prevent the model from handling edge cases gracefully. The best system prompts clearly articulate what the model should do, what it should not do, and how it should handle ambiguous or unexpected inputs. They read like a well-written job description for a highly competent employee who needs clear guidance but also the latitude to exercise judgement.

## Instructing the Model

When you instruct the model to follow a particular pattern or adopt a specific behaviour, the phrasing of your instruction matters significantly. Imperative statements tend to be more effective than suggestions: "Always include a source citation" produces more consistent compliance than "It would be nice to include source citations." Similarly, providing concrete examples of desired output is often more effective than abstract descriptions of what you want. This technique, known as few-shot prompting, leverages the model's in-context learning ability by showing it exactly what success looks like before asking it to produce its own output.

## The System Message Sets the Behaviour

Understanding that the system message sets the behaviour of the model for the entire conversation is critical for building reliable applications. Every response the model generates is influenced by the instructions in the system message, even if the user's query does not directly relate to those instructions. This means that investments in system prompt quality pay dividends across every interaction. Teams that treat their system prompt as production code, subject to version control, peer review, and regression testing, consistently build more reliable LLM applications than those who treat it as an afterthought.

## Prompt Chaining and Context Management

In complex applications, a single prompt is rarely sufficient. Prompt chaining involves breaking a complex task into a sequence of simpler prompts, where the output of one step becomes the input to the next. When implementing prompt chains, it is common to ignore previous context at certain stages in order to prevent information from earlier steps from contaminating later ones. For example, a summarisation pipeline might first extract key facts from a document, then discard the original document and generate a summary from only the extracted facts. This deliberate narrowing of context reduces the risk of hallucination and improves the consistency of the final output.

## Evaluation and Iteration

Prompt engineering is inherently iterative. No prompt is perfect on the first draft, and the only way to assess quality is through systematic evaluation against a representative set of test cases. Effective evaluation requires defining clear success criteria before testing begins, running the prompt against a diverse corpus of inputs, and analysing failures to identify patterns that suggest specific improvements. Automated evaluation using rubrics scored by a separate LLM instance has emerged as a practical approach for teams that need to evaluate prompts at scale, though human review remains essential for catching subtle quality issues that automated scoring may miss.

## Common Pitfalls

The most frequent mistake in prompt engineering is assuming that natural language instructions will be interpreted exactly as intended. Language is inherently ambiguous, and models may interpret an instruction differently than the prompt author expected. Another common pitfall is overloading a single prompt with too many instructions, which can cause the model to prioritise some instructions over others in unpredictable ways. Breaking complex instruction sets into hierarchical sections with clear headings and explicit priority ordering helps the model allocate its attention appropriately and reduces the incidence of instruction-following failures.
