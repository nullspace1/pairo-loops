This document deals with project invariants that must be enforced throughout the development lifecycle.

1) Object oriented programming: Solutions must use object-oriented programming principles: Single Responsibility Principle, Encapsulation, Polymorphism, Abstraction

2) No general functions: Solutions must solve the problems using primarily objects, not isolated functions. Avoid the usage of static functions as well.

3) No type conditionals: Prefer polymorphism or an interface over type checking.

4) Tests and type checks: All tests must pass, and type checks must return no errors after every change.

5) Strong types: Use always the strongest type possible for a given variable or class. Avoid using Any, object, or other nonspecific type.

6) Readability: Code should be easy to verify: Long conditionals should be replaced by private methods, long function blocks should be broken up into an object or methods.

7) Reusability: Prefer existing libraries over new code. Isolate specific utility functions into reusable utility classes over static or isolated functions.