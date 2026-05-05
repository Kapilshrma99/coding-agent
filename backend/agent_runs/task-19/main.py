def is_prime(num):
    if num <= 1:
        return False
    for i in range(2, int(num ** 0.5) + 1):
        if num % i == 0:
            return False
    return True

def check_primes(start, end):
    prime_numbers = []
    for number in range(start, end + 1):
        if is_prime(number):
            prime_numbers.append(number)
    return prime_numbers

if __name__ == '__main__':
    start = int(input('Enter the start of the range: '))
    end = int(input('Enter the end of the range: '))
    primes = check_primes(start, end)
    print(f'Prime numbers between {start} and {end}: {primes}')