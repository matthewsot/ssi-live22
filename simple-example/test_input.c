#include <foobar.h>

// TODO: Implement foo!!

void main() {
    int x = 10;
    x = x * 5;
    ssi_print(x);

    int x = foo();
    x = x * 5;
    ssi_print(x);
    ssi_explain(x);
}
