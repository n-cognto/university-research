let index = 0;
const slides = document.querySelectorAll(".slides img");

function showSlides() {
    slides.forEach((slide, i) => {
        slide.style.display = (i === index) ? "block" : "none";
    });
    index = (index + 1) % slides.length;
    setTimeout(showSlides, 3000);
}

showSlides();
