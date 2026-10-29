from pydantic import BaseModel, Field, field_validator


class GenerateRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Question cannot be blank.")
        return value


class ExecuteRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=8000)
    question: str = Field(default="", max_length=1000)

    @field_validator("sql")
    @classmethod
    def sql_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("SQL cannot be blank.")
        return value