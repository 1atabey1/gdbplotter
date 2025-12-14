
#include <stdio.h>
#ifdef _WIN32
    #include <windows.h>
#else
    #include <unistd.h>
#endif

void wait( int seconds )
{
    #ifdef _WIN32
        Sleep( 1000 * seconds );
    #else
        sleep( seconds );
    #endif
}

int main()
{
    volatile unsigned int test = 0;

    while (1) {
        test++;
        printf("test: %d", test);
        wait(1);
    }
}
